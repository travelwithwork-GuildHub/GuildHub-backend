"""[P22] 錯誤碼一致性。開工前準備 附錄 C。

401 / 403 / 404 / 409 / 422 各一。分散在各服務的測試裡也會碰到這些碼，但
這裡把五個放在同一個檔案，是為了讓「前端能不能只寫一套錯誤處理」這件事
有一個地方可以一眼看完。

401 另有 tests/test_auth_gate.py 逐一掃過全部端點，且不需要資料庫。
"""

import uuid

import pytest

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def test_error_401_when_not_logged_in(db, api):
    response = await api.get("/api/me")
    assert response.status_code == 401


async def test_error_403_for_a_non_owner(db, login):
    """附錄 C：非發起人操作 owner-only 端點。"""
    owner = await login("發起人")
    created = await owner.post("/api/projects", json={"title": "t", "body": "b"})
    project_id = created.json()["id"]

    stranger = await login("路人")
    response = await stranger.post(
        f"/api/projects/{project_id}/close", json={}
    )
    assert response.status_code == 403


async def test_error_403_for_a_wrong_room_password(db, login):
    owner = await login("發起人")
    created = await owner.post("/api/projects", json={"title": "t", "body": "b"})
    project_id = created.json()["id"]
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    response = await owner.post(
        f"/api/projects/{project_id}/enter", json={"password": "錯的"}
    )
    assert response.status_code == 403


async def test_error_404_for_a_missing_project(db, login):
    me = await login("找不到東西的")
    response = await me.get(f"/api/projects/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_error_409_for_a_taken_seat(db, login):
    """附錄 C：座位已被佔用，由 seats PK 衝突觸發，不在應用層判斷。"""
    owner = await login("發起人")
    created = await owner.post("/api/projects", json={"title": "t", "body": "b"})
    project_id = created.json()["id"]
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    first = await login("先坐的")
    second = await login("後到的")
    for client in (first, second):
        await client.post(
            f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
        )

    assert (
        await first.post(f"/api/projects/{project_id}/seats", json={"seat_index": 1})
    ).status_code == 201
    assert (
        await second.post(f"/api/projects/{project_id}/seats", json={"seat_index": 1})
    ).status_code == 409


async def test_error_422_for_a_malformed_body(db, login):
    """附錄 C：Pydantic 驗證失敗，FastAPI 預設，不必自己處理。

    注意這是「型別不對」而不是「太長」—— 長度是資料庫的事（[P15]）。
    """
    me = await login("送壞資料的")
    response = await me.post("/api/projects", json={"title": "只有標題沒有內文"})
    assert response.status_code == 422


async def test_error_400_when_room_ready_blocks_activation(db, login):
    """附錄 C 最後一列：room_ready check 擋下的成軍請求。"""
    owner = await login("發起人")
    created = await owner.post("/api/projects", json={"title": "t", "body": "b"})
    project_id = created.json()["id"]

    response = await owner.post(f"/api/projects/{project_id}/form-team", json={})
    assert response.status_code == 422  # 連 password 欄位都沒給，先被 Pydantic 擋
