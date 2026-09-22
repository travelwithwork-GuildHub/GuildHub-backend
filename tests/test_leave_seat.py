"""[BE-G39] 退位：DELETE /api/projects/{project_id}/seats。

2026-09-22 P1 裁決（前端「給後端-座位退位與mine端點」）。規格書 §6.3 原本是
「一旦入座即固定，直到專案結束才解除」—— demo 現場的實際體感是：人從工位
站起來走掉了，那一格卻永遠顯示有人、別人坐不下、他自己也不能改坐別格。

翻案的範圍只有「退自己的位」：

  · 只刪自己的那一格，不吃 body、不指定 seat_index。
    **發起人釋放別人座位（BE-G07）仍然不做**，FORBIDDEN 裡的
    DELETE .../seats/{seat_index} 沒有動
  · 冪等：本來就沒坐、重複退位，都回 204
  · 換位 = 退位 + 重新入座，沒有換位端點
  · 結案後回 409 project_closed，跟入座同一組碼

退位之後「我的房間」（BE-G38）就不再列這間 —— 除非自己是發起人。「所屬」
仍然只是兩個資料庫事實的聯集；要記得「坐過」就得存歷史，那是成員制。
"""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def open_room(login, seat_count: int = 4) -> tuple[str, object]:
    owner = await login("發起人")
    created = await owner.post(
        "/api/projects",
        json={"title": "退位測試", "body": "內文", "seat_count": seat_count},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    formed = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert formed.status_code == 200, formed.text
    return project_id, owner


async def enter(client, project_id: str) -> None:
    response = await client.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert response.status_code == 200, response.text


async def sit(client, project_id: str, seat_index: int):
    return await client.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": seat_index}
    )


async def leave(client, project_id: str):
    return await client.delete(f"/api/projects/{project_id}/seats")


async def seats_of(client, project_id: str) -> dict[int, str]:
    response = await client.get(f"/api/projects/{project_id}/seats")
    assert response.status_code == 200, response.text
    return {s["seat_index"]: s["user_id"] for s in response.json()}


async def test_leaving_frees_the_seat_for_someone_else(login):
    """退位之後那一格恢復可坐，別人坐得下。"""
    project_id, _owner = await open_room(login)
    alice = await login("甲")
    bob = await login("乙")
    await enter(alice, project_id)
    await enter(bob, project_id)

    assert (await sit(alice, project_id, 1)).status_code == 201
    assert (await sit(bob, project_id, 1)).status_code == 409

    response = await leave(alice, project_id)
    assert response.status_code == 204, response.text
    assert response.content == b""

    assert await seats_of(bob, project_id) == {}
    assert (await sit(bob, project_id, 1)).status_code == 201


async def test_leave_then_sit_elsewhere_is_how_you_switch(login):
    """換位 = 退位 + 重新入座。退位前是 already_seated，退位後就坐得下。"""
    project_id, _owner = await open_room(login)
    alice = await login("甲")
    await enter(alice, project_id)
    await sit(alice, project_id, 0)

    blocked = await sit(alice, project_id, 2)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "already_seated"

    assert (await leave(alice, project_id)).status_code == 204
    moved = await sit(alice, project_id, 2)
    assert moved.status_code == 201, moved.text

    me = moved.json()["user_id"]
    assert await seats_of(alice, project_id) == {2: me}


async def test_leave_only_removes_my_own_seat(login):
    """只刪自己的。同房間其他人的座位一格都不動。"""
    project_id, owner = await open_room(login)
    alice = await login("甲")
    bob = await login("乙")
    for client in (owner, alice, bob):
        await enter(client, project_id)
    await sit(owner, project_id, 0)
    await sit(alice, project_id, 1)
    bobs = (await sit(bob, project_id, 2)).json()["user_id"]
    owners = (await seats_of(owner, project_id))[0]

    # 發起人退位也只退自己 —— 不是「發起人釋放任一座位」（BE-G07 仍然不做）
    assert (await leave(owner, project_id)).status_code == 204
    assert (await leave(alice, project_id)).status_code == 204

    remaining = await seats_of(bob, project_id)
    assert remaining == {2: bobs}
    assert owners not in remaining.values()


async def test_leave_is_idempotent(login):
    """本來就沒坐、重複退位，都是 204，不是 4xx。"""
    project_id, _owner = await open_room(login)
    alice = await login("甲")
    await enter(alice, project_id)

    assert (await leave(alice, project_id)).status_code == 204  # 從沒坐過

    await sit(alice, project_id, 3)
    assert (await leave(alice, project_id)).status_code == 204
    assert (await leave(alice, project_id)).status_code == 204  # 重複


async def test_leave_does_not_touch_other_rooms(login):
    """退的是「這個 project」的座位，不是我在所有房間的座位。"""
    here, _ = await open_room(login)
    there, _ = await open_room(login)
    alice = await login("甲")
    for project_id in (here, there):
        await enter(alice, project_id)
        await sit(alice, project_id, 0)

    assert (await leave(alice, here)).status_code == 204

    assert await seats_of(alice, here) == {}
    assert list((await seats_of(alice, there)).keys()) == [0]


async def test_concurrent_leave_and_claim_leaves_exactly_one_occupant(db, login):
    """退位的同時有人搶那一格：不論先後，最後那一格至多一人、且不是退位的人。

    退位是單一句 DELETE，搶位仍然由 seats 的 primary key 擋 —— 沒有新增
    「先查再寫」，所以也沒有新的競態。
    """
    project_id, _owner = await open_room(login)
    leaver = await login("要走的人")
    await enter(leaver, project_id)
    await sit(leaver, project_id, 0)
    leaver_id = (await seats_of(leaver, project_id))[0]

    contenders = [await login(f"搶手{i}") for i in range(4)]
    for client in contenders:
        await enter(client, project_id)

    responses = await asyncio.gather(
        leave(leaver, project_id),
        *(sit(client, project_id, 0) for client in contenders),
    )
    assert responses[0].status_code == 204
    claims = sorted(r.status_code for r in responses[1:])
    assert claims.count(201) <= 1, claims
    assert set(claims) <= {201, 409}, claims

    rows = await db.fetch(
        "select user_id from seats where project_id = $1 and seat_index = 0",
        uuid.UUID(project_id),
    )
    assert len(rows) == claims.count(201)
    assert all(str(r["user_id"]) != leaver_id for r in rows)


async def test_leave_after_close_is_project_closed(login):
    """結案之後退位回 409 project_closed，跟入座同一組碼。"""
    project_id, owner = await open_room(login)
    alice = await login("甲")
    await enter(alice, project_id)
    await sit(alice, project_id, 1)

    closed = await owner.post(f"/api/projects/{project_id}/close")
    assert closed.status_code == 200, closed.text

    response = await leave(alice, project_id)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "project_closed"


async def test_leave_requires_room_token(login):
    """跟入座同一道門：沒 enter 過就是 403 no_room_token。"""
    project_id, _owner = await open_room(login)
    stranger = await login("路人")

    response = await leave(stranger, project_id)
    assert response.status_code == 403
    assert response.json()["code"] == "no_room_token"


async def test_after_leaving_the_room_is_no_longer_mine(login):
    """「所屬」= 發起人 ∪ 目前有座位。退位的隊員不再列；發起人照列。

    這是刻意的：要讓退位的人繼續列在「我的房間」，就得記住「坐過」這段
    歷史 —— 那是成員制，§6.2 沒有被翻案。
    """
    project_id, owner = await open_room(login)
    member = await login("隊員")
    await enter(member, project_id)
    await enter(owner, project_id)
    await sit(member, project_id, 1)
    await sit(owner, project_id, 0)

    await leave(member, project_id)
    await leave(owner, project_id)

    async def mine(client) -> set[str]:
        response = await client.get("/api/rooms", params={"mine": "true"})
        assert response.status_code == 200, response.text
        return {door["project_id"] for door in response.json()}

    assert await mine(member) == set()
    assert await mine(owner) == {project_id}
