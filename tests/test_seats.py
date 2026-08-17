"""SeatService。規格書 §4.1 §6.3。

[P38] 是全案最難寫也最必要的測試：任務表註明「必須真正並行發送，不得循序
模擬」。循序模擬會讓「先查再寫」的錯誤寫法也通過 —— 那正是這個測試要抓的
東西，所以循序版本等於沒測。

這裡用 asyncio.gather 讓 N 個各自有 session 的 client 同時打同一格座位，
真正在資料庫層面撞在一起。
"""

import asyncio
import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def open_room(login, seat_count: int = 8) -> tuple[str, object]:
    """開一個已成軍的房間，回傳 (project_id, 發起人的 client)。"""
    owner = await login("發起人")
    created = await owner.post(
        "/api/projects",
        json={"title": "搶位子測試", "body": "內文", "seat_count": seat_count},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    formed = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert formed.status_code == 200, formed.text
    return project_id, owner


async def enter(client, project_id: str) -> None:
    """通過房間密碼，把 room token 放進這個 client 的 session。"""
    response = await client.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------- [P38] 兩人搶同一座位


async def test_seat_race_exactly_one_winner(db, login):
    """八個人同時搶第 0 格：恰好一個 201，其餘全部 409。

    這是規格書 §4.1 的核心主張的實測 —— 不變式寫在資料庫，因為應用層會有
    bug，約束不會。
    """
    project_id, _owner = await open_room(login)

    contenders = [await login(f"搶手{i}") for i in range(8)]
    for client in contenders:
        await enter(client, project_id)

    responses = await asyncio.gather(
        *(
            client.post(f"/api/projects/{project_id}/seats", json={"seat_index": 0})
            for client in contenders
        )
    )

    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1, f"應該恰好一個成功，實際 {codes}"
    assert codes.count(409) == len(contenders) - 1, f"其餘都該是 409，實際 {codes}"

    seated = await db.fetch(
        "select * from seats where project_id = $1 and seat_index = 0", uuid.UUID(project_id)
    )
    assert len(seated) == 1, "資料庫裡不該有兩列"


async def test_seat_race_different_seats_all_succeed(db, login):
    """反面：搶不同格子的人應該全部成功。

    沒有這個測試的話，「一律回 409」也能讓上面那個測試通過。
    """
    project_id, _owner = await open_room(login)

    contenders = [await login(f"各坐各的{i}") for i in range(4)]
    for client in contenders:
        await enter(client, project_id)

    responses = await asyncio.gather(
        *(
            client.post(f"/api/projects/{project_id}/seats", json={"seat_index": i})
            for i, client in enumerate(contenders)
        )
    )

    assert [r.status_code for r in responses] == [201] * 4


# ------------------------------------------------------- [P39] 一人佔兩個座位


async def test_seat_dup_user_second_claim_is_rejected(db, login):
    """同一個 user 認領第二個座位回 409，由 unique (project_id, user_id) 擋下。"""
    project_id, _owner = await open_room(login)

    member = await login("想佔兩格的")
    await enter(member, project_id)

    first = await member.post(f"/api/projects/{project_id}/seats", json={"seat_index": 0})
    assert first.status_code == 201, first.text

    second = await member.post(f"/api/projects/{project_id}/seats", json={"seat_index": 1})
    assert second.status_code == 409, second.text

    count = await db.fetchval(
        "select count(*) from seats where project_id = $1 and user_id = $2",
        uuid.UUID(project_id),
        uuid.UUID(member.user_id),
    )
    assert count == 1


async def test_seat_dup_user_is_the_unique_constraint_not_the_pk(db, login):
    """兩種 409 要分得出來，否則前端沒辦法給對的訊息。"""
    project_id, _owner = await open_room(login)
    member = await login("同一人")
    await enter(member, project_id)

    await member.post(f"/api/projects/{project_id}/seats", json={"seat_index": 0})
    response = await member.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": 3}
    )
    assert response.status_code == 409
    assert "已經" in response.json()["detail"]


async def test_seat_out_of_range_is_rejected_by_the_database(db, login):
    """seat_in_range check（0 ≤ seat_index < 8）。"""
    project_id, _owner = await open_room(login)
    member = await login("坐到牆外的")
    await enter(member, project_id)

    response = await member.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": 99}
    )
    assert response.status_code == 400


# ------------------------------------------------- 座位數上限（P1 裁決後加入）
#
# 規格書 §6.3：房間人數上限 = 座位數。這條約束跨兩張表，check 寫不出來，
# 所以擋在 claim 的 insert ... where 裡。


async def test_seat_beyond_seat_count_is_rejected(db, login):
    """seat_count = 2 的房間不該接受 seat_index = 7。

    這是加上容量檢查之前會通過的請求 —— 房間因此可以塞進 8 個人。
    """
    project_id, _owner = await open_room(login, seat_count=2)
    member = await login("想坐第七格的")
    await enter(member, project_id)

    response = await member.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": 7}
    )
    assert response.status_code == 400, response.text
    assert "2" in response.json()["detail"]


async def test_seat_within_seat_count_still_works(db, login):
    """反面：容量內的座位不能被誤擋。"""
    project_id, _owner = await open_room(login, seat_count=2)
    a = await login("坐第零格")
    b = await login("坐第一格")
    for client in (a, b):
        await enter(client, project_id)

    assert (
        await a.post(f"/api/projects/{project_id}/seats", json={"seat_index": 0})
    ).status_code == 201
    assert (
        await b.post(f"/api/projects/{project_id}/seats", json={"seat_index": 1})
    ).status_code == 201


async def test_room_is_full_when_every_seat_is_taken(db, login):
    """§6.3：座位滿即房間滿。第三個人在兩人房裡無論選哪一格都進不去。"""
    project_id, _owner = await open_room(login, seat_count=2)
    a, b, c = [await login(f"成員{i}") for i in range(3)]
    for client in (a, b, c):
        await enter(client, project_id)

    await a.post(f"/api/projects/{project_id}/seats", json={"seat_index": 0})
    await b.post(f"/api/projects/{project_id}/seats", json={"seat_index": 1})

    for seat_index, expected in ((0, 409), (1, 409), (2, 400), (7, 400)):
        response = await c.post(
            f"/api/projects/{project_id}/seats", json={"seat_index": seat_index}
        )
        assert response.status_code == expected, (
            f"seat_index={seat_index} 回了 {response.status_code}，預期 {expected}"
        )


async def test_capacity_check_did_not_weaken_the_race_protection(db, login):
    """加了容量檢查之後，同一格的競爭仍然只能有一個贏家。

    容量檢查查的是 projects.seat_count，競爭防護靠的是 seats 的 PK ——
    兩者正交。這個測試就是釘住「正交」這件事。
    """
    project_id, _owner = await open_room(login, seat_count=4)

    contenders = [await login(f"搶手{i}") for i in range(6)]
    for client in contenders:
        await enter(client, project_id)

    responses = await asyncio.gather(
        *(
            client.post(f"/api/projects/{project_id}/seats", json={"seat_index": 1})
            for client in contenders
        )
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1, f"容量檢查破壞了競爭防護：{codes}"
    assert codes.count(409) == 5


# 註：claim 在 where 沒命中時會分辨「房間沒這格」(400) 與「根本沒這個房間」
# (404)，但 404 那一支在實務上到不了 —— require_room_token 需要 session 裡有
# room token，而 token 只由 /enter 簽發，/enter 對不存在的專案已經先回 404。
# 這裡不為一條到不了的分支硬寫測試，留著這段說明即可。


# ------------------------------------------------------------ [P41] 座位查詢


async def test_seat_list_reports_occupancy(db, login):
    project_id, owner = await open_room(login)
    await enter(owner, project_id)
    await owner.post(f"/api/projects/{project_id}/seats", json={"seat_index": 2})

    listed = await owner.get(f"/api/projects/{project_id}/seats")
    assert listed.status_code == 200
    rows = listed.json()
    assert [r["seat_index"] for r in rows] == [2]
    assert rows[0]["user_id"] == owner.user_id


async def test_seat_list_of_an_empty_room_is_empty(db, login):
    """房間初始全為空位（§6.3）。空房間不是錯誤。"""
    project_id, owner = await open_room(login)
    await enter(owner, project_id)

    listed = await owner.get(f"/api/projects/{project_id}/seats")
    assert listed.status_code == 200
    assert listed.json() == []


async def test_seat_endpoints_require_the_room_token(db, login):
    """沒通過房間密碼就看不到座位（§6.2）。"""
    project_id, _owner = await open_room(login)
    outsider = await login("沒進過房間的")

    listed = await outsider.get(f"/api/projects/{project_id}/seats")
    assert listed.status_code == 403

    claimed = await outsider.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": 0}
    )
    assert claimed.status_code == 403


# --------------------------------------------- 約束層面的競爭（不經過應用層）


async def test_seat_race_at_the_database_level(db):
    """同一件事，但繞過整個應用層直接撞資料庫。

    上面那個測試若因為應用層的某個巧合而通過，這個不會 —— 它證明保護來自
    約束本身，不是來自 FastAPI。
    """
    owner_id = uuid.uuid4()
    await db.execute(
        "insert into profiles (id, display_name) values ($1, $2)", owner_id, "發起人"
    )
    project_id = await db.fetchval(
        "insert into projects (owner_id, title, body) values ($1, 't', 'b') returning id",
        owner_id,
    )

    people = []
    for i in range(8):
        user_id = uuid.uuid4()
        await db.execute(
            "insert into profiles (id, display_name) values ($1, $2)", user_id, f"人{i}"
        )
        people.append(user_id)

    async def claim(user_id):
        try:
            await db.execute(
                "insert into seats (project_id, seat_index, user_id) values ($1, 0, $2)",
                project_id,
                user_id,
            )
            return "ok"
        except asyncpg.UniqueViolationError:
            return "conflict"

    results = await asyncio.gather(*(claim(u) for u in people))
    assert results.count("ok") == 1, f"約束沒擋住：{results}"
