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
