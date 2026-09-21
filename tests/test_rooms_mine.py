"""[BE-G38] 我的房間：GET /api/rooms?mine=true。

2026-09-21 P1 裁決（前端單一入口，清單優先顯示「自己所屬的房間」）。

§6.2 是「共享密碼，不做成員制」—— 後端沒有成員表，這個裁決也沒有新增一個。
「所屬」只用兩個早就在資料庫裡的事實定義：

  · 我是發起人（projects.owner_id）
  · 我坐過位子（seats 裡有我這一列）

**只通過密碼、沒有坐下的，不算。** 那張 room token 是 session 裡的東西，
不在資料庫，8 小時後就過期 —— 拿它當「所屬」，清單就會隨時間自己變。

不帶參數時的行為一個字都不變：走廊上所有成軍中的門。
"""

import pytest

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def form_room(client, title: str) -> str:
    """由 client 發起並成軍一個房間，回傳 project_id。"""
    created = await client.post(
        "/api/projects", json={"title": title, "body": "內文", "seat_count": 4}
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    formed = await client.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert formed.status_code == 200, formed.text
    return project_id


async def enter(client, project_id: str) -> None:
    response = await client.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert response.status_code == 200, response.text


async def sit(client, project_id: str, seat_index: int) -> None:
    response = await client.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": seat_index}
    )
    assert response.status_code == 201, response.text


async def mine(client) -> set[str]:
    response = await client.get("/api/rooms", params={"mine": "true"})
    assert response.status_code == 200, response.text
    return {door["project_id"] for door in response.json()}


async def test_my_own_room_is_mine_without_sitting(login):
    """發起人不必坐下，自己成軍的房間就算自己的。"""
    owner = await login("發起人")
    project_id = await form_room(owner, "我開的房")

    assert await mine(owner) == {project_id}


async def test_room_where_i_sat_is_mine(login):
    """坐過位子的房間算自己的 —— 這是「自己加入的」的定義。"""
    owner = await login("發起人")
    project_id = await form_room(owner, "別人開的房")

    member = await login("隊員")
    await enter(member, project_id)
    await sit(member, project_id, 1)

    assert await mine(member) == {project_id}


async def test_entering_without_sitting_is_not_mine(login):
    """只通過密碼、沒坐下，不算 —— room token 不在資料庫，會過期。"""
    owner = await login("發起人")
    project_id = await form_room(owner, "只是路過")

    visitor = await login("路人")
    await enter(visitor, project_id)

    assert await mine(visitor) == set()


async def test_other_peoples_rooms_are_not_mine(login):
    """同時存在別人的房間時，只回自己的那幾間。"""
    alice = await login("甲")
    bob = await login("乙")
    alices = await form_room(alice, "甲的房")
    bobs = await form_room(bob, "乙的房")

    assert await mine(alice) == {alices}
    assert await mine(bob) == {bobs}


async def test_closed_room_is_not_mine(login):
    """結案的房間門已經從走廊移除（§6.1），「我的房間」也不列。"""
    owner = await login("發起人")
    project_id = await form_room(owner, "要結案的房")

    closed = await owner.post(f"/api/projects/{project_id}/close")
    assert closed.status_code == 200, closed.text

    assert await mine(owner) == set()


async def test_without_mine_every_door_is_listed(login):
    """不帶參數：照舊列出走廊上所有成軍中的門，不因為這個功能而改變。"""
    alice = await login("甲")
    bob = await login("乙")
    alices = await form_room(alice, "甲的房")
    bobs = await form_room(bob, "乙的房")

    for params in ({}, {"mine": "false"}):
        response = await alice.get("/api/rooms", params=params)
        assert response.status_code == 200, response.text
        doors = {door["project_id"] for door in response.json()}
        assert doors == {alices, bobs}, params


async def test_mine_requires_login(api):
    """未登入一律 401，跟不帶參數時一樣。"""
    response = await api.get("/api/rooms", params={"mine": "true"})
    assert response.status_code == 401
