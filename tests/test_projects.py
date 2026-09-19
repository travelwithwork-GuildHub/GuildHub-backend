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

    2026-09-08 P1 裁決（選項 A）：closed 保留專案列，座位由 close_project()
    在同一交易內以一句 delete 釋放，不走 cascade。規格書 §4.1 已改正。

    **裁決之後這條測試更重要，不是更不重要。** 座位釋放從「資料庫保證的
    不變式」降級成「應用層的責任」—— 下面兩個斷言是唯一會擋下漏刪的東西。
    仍然只驗結果（座位消失、專案還在），不綁定實作手段。
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


# ------------------------------------------- [BE-G31] 結案的案子不能再成軍
#
# 前端 2026-09-19 的清單第 1.2 條：對 closed 的案子打 form-team 回 200，
# status 回到 active，GET /api/rooms 又列出這扇門 —— 也就是「結案」可以被
# 發起人無限次撤銷。form_team() 的 update 沒有帶狀態條件，require_owner
# 也只驗 owner 不驗狀態。
#
# 裁決（2026-09-19）：recruiting 與 active 都允許，closed 擋。
# active 再成軍 = 換密碼，那是前端 FE-J04 正在用的行為，**不是漏網之魚**：
# 被砍掉的是獨立的 reset-password 端點，不是這個行為。下面第二條測試就是
# 用來擋「哪天有人順手把 active 也收緊」的。


async def test_forming_a_team_twice_still_rotates_the_password(db, login):
    """active 再成軍 = 換密碼，維持 200。這是受保護的行為，不是 bug。"""
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    again = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": "newpass123"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "active"

    member = await login("拿舊密碼的")
    with_old = await member.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert with_old.status_code == 403, "舊密碼必須失效，否則換密碼沒有意義"

    with_new = await member.post(
        f"/api/projects/{project_id}/enter", json={"password": "newpass123"}
    )
    assert with_new.status_code == 200, with_new.text


async def test_a_closed_project_cannot_be_resurrected(db, login):
    """第 1.2 條本身：結案之後不得再被成軍。"""
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})
    closed = await owner.post(f"/api/projects/{project_id}/close", json={})
    assert closed.status_code == 200, closed.text

    response = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )

    assert response.status_code == 409, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "project_closed"

    still = await owner.get(f"/api/projects/{project_id}")
    assert still.json()["status"] == "closed", "狀態不得被那次失敗的請求改掉"


async def test_a_closed_project_stays_off_the_corridor(db, login):
    """門從走廊移除（§6.1）之後，不得因為一次 form-team 又長回來。"""
    owner = await login("發起人")
    project_id = await new_project(owner)
    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})
    await owner.post(f"/api/projects/{project_id}/close", json={})

    await owner.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})

    doors = await owner.get("/api/rooms")
    assert project_id not in [d["project_id"] for d in doors.json()]


# --------------------------------------------- [BE-G33] 房間密碼的長度下限
#
# 前端清單第 1.6 條（他們自己標「可選」）：3 個字的密碼也 200。
#
# 這一條是全案**唯一**該寫在 Pydantic 的長度規則 —— 明文密碼不會進資料庫，
# SQL 無從驗起（models.py 的 RegisterIn.password 早就是這樣，註解裡寫明它是
# 「不得在應用層重複實作長度檢查」的唯一例外）。所以它回 422，不是 400。


@pytest.mark.parametrize("password", ["", "abc", "x" * 65])
async def test_room_password_length_is_enforced(db, login, password):
    owner = await login("發起人")
    project_id = await new_project(owner)

    response = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": password}
    )

    assert response.status_code == 422, f"回了 {response.status_code}：{response.text}"


@pytest.mark.parametrize("password", ["1234", "x" * 64])
async def test_room_password_boundaries_are_allowed(db, login, password):
    """4 與 64 是上下限本身，必須過 —— 數字跟前端 limits.ts 同源。"""
    owner = await login("發起人")
    project_id = await new_project(owner)

    response = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": password}
    )

    assert response.status_code == 200, response.text


async def test_account_password_keeps_its_own_longer_minimum(db, api):
    """房間密碼放寬到 4，不得把帳號密碼一起放寬。

    passwords.py 的 docstring：兩種密碼共用雜湊函式沒問題，共用心智模型會
    出事。房間密碼是口頭傳的共享密碼，帳號密碼保護的是一個身分。
    """
    response = await api.post(
        "/api/register",
        json={"login_id": "shortpw", "password": "1234", "nickname": "短密碼"},
    )

    assert response.status_code == 422
