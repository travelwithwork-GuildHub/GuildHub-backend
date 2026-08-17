"""ProjectService。規格書 §5.4 §6.1 §6.2。

房間不是被認領的，是成軍的副作用（§6.1）。
"""

import uuid

import pytest

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def new_project(client, **overrides) -> str:
    payload = {"title": "測試專案", "body": "內文"} | overrides
    response = await client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ------------------------------------------------------------ [P18] 任務看板


async def test_project_list_shows_a_new_recruiting_post(db, login):
    owner = await login("發起人")
    project_id = await new_project(owner, title="找人一起做小工具")

    listed = await owner.get("/api/projects")
    assert listed.status_code == 200
    assert project_id in [p["id"] for p in listed.json()]


async def test_project_list_hides_expired_posts(db, login):
    """§5.4 逾期未處理自動下架。

    「自動下架」是查詢時過濾的結果，不是背景排程 —— 貼文到期提醒與自動下架
    排程已於 WBS v0.2 砍除（守則 §3）。少一個排程器，也少一種會在半夜壞掉
    的東西。
    """
    owner = await login("發起人")
    project_id = await new_project(owner, title="早就過期的貼文")

    await db.execute(
        "update projects set expires_at = now() - interval '1 day' where id = $1",
        uuid.UUID(project_id),
    )

    listed = await owner.get("/api/projects")
    assert project_id not in [p["id"] for p in listed.json()]


async def test_project_list_only_shows_recruiting(db, login):
    """成軍後貼文從任務看板下架（§6.1）。"""
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    listed = await owner.get("/api/projects")
    assert project_id not in [p["id"] for p in listed.json()]


async def test_project_expires_at_comes_from_the_database_default(db, login):
    """[P19]：expires_at 用 DB 預設值（now() + 7 days），不在應用層算。"""
    owner = await login("發起人")
    project_id = await new_project(owner)

    days = await db.fetchval(
        "select extract(day from (expires_at - now())) from projects where id = $1",
        uuid.UUID(project_id),
    )
    assert 6 <= days <= 7


async def test_project_get_returns_404_for_a_stranger(db, login):
    owner = await login("發起人")
    response = await owner.get(f"/api/projects/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_project_never_exposes_the_password_hash(db, login):
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    fetched = await owner.get(f"/api/projects/{project_id}")
    assert "password_hash" not in fetched.json()


# --------------------------------------------------------- [P30] owner-only


async def test_require_owner_rejects_a_non_owner(db, login):
    owner = await login("發起人")
    stranger = await login("路人")
    project_id = await new_project(owner)

    for path in ("form-team", "close"):
        response = await stranger.post(
            f"/api/projects/{project_id}/{path}", json={"password": PASSWORD}
        )
        assert response.status_code == 403, f"{path} 回了 {response.status_code}"


async def test_require_owner_returns_404_before_403_for_missing_projects(db, login):
    stranger = await login("路人")
    response = await stranger.post(
        f"/api/projects/{uuid.uuid4()}/close", json={}
    )
    assert response.status_code == 404


# ------------------------------------------------------------- [P32] 成軍


async def test_form_team_sets_template_and_password_together(db, login):
    """§6.1：成軍時系統指派房間模板、發起人設定密碼，同一個交易內完成。"""
    owner = await login("發起人")
    project_id = await new_project(owner)

    response = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"

    row = await db.fetchrow(
        "select status, room_template, password_hash from projects where id = $1",
        uuid.UUID(project_id),
    )
    assert row["status"] == "active"
    assert row["room_template"] is not None
    assert row["password_hash"] is not None


async def test_room_ready_check_blocks_activation_without_a_room(db, login):
    """[P32] 的重點：必須斷言是 room_ready check 擋下的。

    繞過應用層直接改 status，資料庫仍然要擋。這證明保護不是來自 form_team
    的實作，而是來自約束本身（§4.1）。
    """
    import asyncpg

    owner = await login("發起人")
    project_id = await new_project(owner)

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "update projects set status = 'active' where id = $1", uuid.UUID(project_id)
        )
    assert "room_ready" in str(exc.value)


# ------------------------------------------------------------- [P34] 結案


async def test_close_releases_every_seat(db, login):
    """§6.1：結案時座位全數釋放。

    ⚠ 這裡有一個規格內部的矛盾待 P1 裁決（見 app/api/projects.py 的註解）：
    §4.1 說機制是 on delete cascade，但 cascade 只在刪除專案列時觸發，而
    §6.1 的生命週期圖顯示 closed 是專案保留下來的狀態。這個測試只驗結果
    （座位消失、專案還在），不綁定實作手段。
    """
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})
    await owner.post(f"/api/projects/{project_id}/enter", json={"password": PASSWORD})
    await owner.post(f"/api/projects/{project_id}/seats", json={"seat_index": 0})

    assert await db.fetchval(
        "select count(*) from seats where project_id = $1", uuid.UUID(project_id)
    ) == 1

    closed = await owner.post(f"/api/projects/{project_id}/close", json={})
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    assert await db.fetchval(
        "select count(*) from seats where project_id = $1", uuid.UUID(project_id)
    ) == 0


async def test_deleting_a_project_cascades_to_seats(db):
    """§4.1 第四列的原始機制：on delete cascade。"""
    owner_id = uuid.uuid4()
    await db.execute(
        "insert into profiles (id, display_name) values ($1, 'o')", owner_id
    )
    project_id = await db.fetchval(
        "insert into projects (owner_id, title, body) values ($1,'t','b') returning id",
        owner_id,
    )
    await db.execute(
        "insert into seats (project_id, seat_index, user_id) values ($1, 0, $2)",
        project_id,
        owner_id,
    )

    await db.execute("delete from projects where id = $1", project_id)
    assert await db.fetchval("select count(*) from seats") == 0


# --------------------------------------------------------- [P36] 進入房間


async def test_room_enter_with_the_right_password_returns_a_token(db, login):
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    member = await login("成員")
    response = await member.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert response.status_code == 200, response.text

    from app import room_token

    claims = room_token.verify(response.json()["room_token"])
    assert claims.project_id == project_id
    assert claims.user_id == member.user_id


async def test_room_enter_with_the_wrong_password_is_403(db, login):
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    member = await login("猜密碼的")
    response = await member.post(
        f"/api/projects/{project_id}/enter", json={"password": "猜錯了"}
    )
    assert response.status_code == 403


async def test_room_enter_before_the_team_is_formed_is_404(db, login):
    """還沒成軍就沒有房間 —— 房間是成軍的副作用（§6.1）。"""
    owner = await login("發起人")
    project_id = await new_project(owner)

    response = await owner.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert response.status_code == 404
