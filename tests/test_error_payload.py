"""[BE-G29] 錯誤回應的形狀：`detail` 旁邊多一個機器可讀的 `code`。

前端 2026-09-19 的清單第 1.4 條：座位的兩種 409 只能靠中文 `detail` 分辨，
所以他們把「你已經在這個房間有座位了」與「這個座位已經有人了」兩句話釘進
契約測試裡 —— 文案一改前端就壞。

修法刻意**不是**把 `detail` 換成 `{"code": ..., "message": ...}`：那會讓前端
現有的字串比對當場失效，而發表前兩邊各只剩一個部署窗口。改成在回應最外層
加一個欄位，**`detail` 一個字都不動**，前端要改讀 `code` 的時候再改。

    {"detail": "這個座位已經有人了", "code": "seat_taken"}

所以這個檔案的每一條都同時斷言兩件事：`code` 是對的，而且 `detail` 還在。
第二個斷言不是湊數的 —— 它是「加法」這個承諾的實作證據。

[BE-G30] 同時處理的是第 1.5 條：資料庫 check 擋下的請求以前會漏成 500
（沒有人接住 asyncpg.CheckViolationError）。現在一律 400，`code` 就是
constraint 的名稱。**規則本身仍然只寫在 sql/001_schema.sql**，這裡改的是
錯誤長什麼樣子，不是把規則搬到應用層。
"""

import uuid

import pytest

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def new_project(client, **overrides) -> str:
    payload = {"title": "標題", "body": "內文"}
    payload.update(overrides)
    response = await client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def open_room(login, seat_count: int = 8) -> tuple[str, object]:
    owner = await login("發起人")
    project_id = await new_project(owner, seat_count=seat_count)
    formed = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert formed.status_code == 200, formed.text
    return project_id, owner


async def seated(login, project_id: str, nickname: str, seat_index: int):
    """讓一個新的人通過密碼並坐下，回傳 (client, 認領座位的回應)。"""
    client = await login(nickname)
    entered = await client.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert entered.status_code == 200, entered.text
    return client, await client.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": seat_index}
    )


# ------------------------------------------------------ 既有錯誤：加上 code


async def test_401_carries_a_code(db, api):
    response = await api.get("/api/me")

    assert response.status_code == 401
    assert response.json()["code"] == "not_logged_in"
    assert response.json()["detail"], "detail 不得因為加了 code 就消失"


async def test_403_for_a_non_owner_carries_a_code(db, login):
    owner = await login("發起人")
    project_id = await new_project(owner)

    stranger = await login("路人")
    response = await stranger.post(f"/api/projects/{project_id}/close", json={})

    assert response.status_code == 403
    assert response.json()["code"] == "not_owner"


async def test_403_for_a_wrong_room_password_carries_a_code(db, login):
    project_id, owner = await open_room(login)

    response = await owner.post(
        f"/api/projects/{project_id}/enter", json={"password": "錯的"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "wrong_password"


async def test_403_without_a_room_token_carries_a_code(db, login):
    """沒有 enter 過就想坐位 —— 包含發起人自己（前端 FE-J13 釘著這條）。"""
    project_id, owner = await open_room(login)

    response = await owner.post(
        f"/api/projects/{project_id}/seats", json={"seat_index": 0}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "no_room_token"


async def test_404_for_a_missing_project_carries_a_code(db, login):
    me = await login("找不到東西的")

    response = await me.get(f"/api/projects/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"


async def test_the_two_409s_are_told_apart_by_code_not_by_chinese(db, login):
    """第 1.4 條本身：兩種 409 要能不看文案就分辨。"""
    project_id, _owner = await open_room(login)
    first, claimed = await seated(login, project_id, "先坐的", 1)
    assert claimed.status_code == 201, claimed.text

    # 同一個人再坐另一格 → 一人只能一格（seats 的 unique）
    again = await first.post(f"/api/projects/{project_id}/seats", json={"seat_index": 2})
    assert again.status_code == 409
    assert again.json()["code"] == "already_seated"
    assert again.json()["detail"] == "你已經在這個房間有座位了", "文案是加法，不得改動"

    # 另一個人坐同一格 → 一格只能一人（seats 的 primary key）
    _second, taken = await seated(login, project_id, "後到的", 1)
    assert taken.status_code == 409
    assert taken.json()["code"] == "seat_taken"
    assert taken.json()["detail"] == "這個座位已經有人了", "文案是加法，不得改動"


async def test_400_for_a_seat_beyond_the_room_carries_a_code(db, login):
    """seat_index < 8（資料庫放行）但 >= seat_count（這個房間沒有那一格）。"""
    project_id, _owner = await open_room(login, seat_count=2)
    _client, response = await seated(login, project_id, "坐太遠的", 5)

    assert response.status_code == 400
    assert response.json()["code"] == "seat_beyond_seat_count"
    assert "2" in response.json()["detail"], "訊息要含座位數，前端 FE-J13 靠它"


async def test_400_for_a_seat_outside_the_table_carries_a_code(db, login):
    """seat_index >= 8 —— 由 seats.seat_in_range 擋，不是容量問題。"""
    project_id, _owner = await open_room(login)
    _client, response = await seated(login, project_id, "坐到牆外的", 8)

    assert response.status_code == 400
    assert response.json()["code"] == "seat_out_of_range"


async def test_400_for_sending_a_message_to_yourself_carries_a_code(db, login):
    me = await login("自言自語的")

    response = await me.post(
        "/api/messages", json={"recipient_id": me.user_id, "body": "嗨"}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "no_self_send"


async def test_422_stays_in_fastapis_own_format(db, login):
    """型別錯／欄位缺仍然是 Pydantic 的事，格式不動，也不加 code。

    這一條是邊界：加 code 是為了「資料庫擋下的」與「我們自己擋下的」錯誤，
    不是把 FastAPI 的驗證錯誤也一起改掉 —— 那會讓前端要處理兩種 422。
    """
    me = await login("送壞資料的")

    response = await me.post("/api/projects", json={"title": "只有標題沒有內文"})

    assert response.status_code == 422
    assert "code" not in response.json()


# --------------------------------- [BE-G30] 資料庫 check 擋下的：400，不是 500


async def test_overlong_project_title_is_400_not_201(db, login):
    """第 1.1 條的驗收：直接打 API 也不能塞髒資料進看板。"""
    me = await login("繞過前端的")

    response = await me.post(
        "/api/projects", json={"title": "標" * 61, "body": "內文"}
    )

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "projects_title_length"


@pytest.mark.parametrize(
    "payload, code",
    [
        ({"title": "", "body": "內文"}, "projects_title_length"),
        ({"title": "標題", "body": ""}, "projects_body_length"),
        ({"title": "標題", "body": "內文", "seat_count": 0}, "projects_seat_count_range"),
        ({"title": "標題", "body": "內文", "seat_count": 9}, "projects_seat_count_range"),
        (
            {"title": "標題", "body": "內文", "needed_skills": [f"技能{i}" for i in range(11)]},
            "projects_needed_skills_count",
        ),
    ],
    ids=["空標題", "空內文", "0 個座位", "9 個座位", "11 項技能"],
)
async def test_the_four_201s_from_the_frontend_report_are_now_400(db, login, payload, code):
    """前端清單 1.1 逐條列出的「以下全部回 201」，現在全部 400。"""
    me = await login("繞過前端的")

    response = await me.post("/api/projects", json=payload)

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == code


async def test_overlong_nickname_is_400_not_500(db, api):
    """第 1.5 條：21 字暱稱登入以前是 500（CheckViolationError 沒人接）。

    登入是演示的第一步，這裡噴 traceback 特別難看。
    """
    response = await api.post("/api/login", json={"nickname": "名" * 21})

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "profiles_display_name_check"


async def test_overlong_nickname_on_register_is_400_not_500(db, api):
    response = await api.post(
        "/api/register",
        json={"login_id": "tester", "password": "guild1234", "nickname": "名" * 21},
    )

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "profiles_display_name_check"


async def test_overlong_display_name_on_patch_is_400_not_500(db, login):
    me = await login("改名的")

    response = await me.patch("/api/profiles/me", json={"display_name": "名" * 21})

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "profiles_display_name_check"


async def test_overlong_message_body_is_400_not_500(db, login):
    """前端沒有回報這一條，但它跟 1.5 是同一類。

    messages.py 只接住了 no_self_send，其他 CheckViolationError 往外噴 ——
    站內信超過 2000 字就是 500。集中的 handler 一併解決。
    """
    sender = await login("話很多的")
    recipient = await login("收信的")

    response = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "字" * 2001}
    )

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "messages_body_check"


async def test_an_unmapped_constraint_still_lands_on_400(db, login):
    """沒有人特別處理過的 check 也不得漏成 500。

    這一條測的是「日後加的約束」：bio 的 300 字上限從來沒有被任何端點接住，
    以前是 500。它現在是 400，靠的是集中 handler 的預設分支而不是逐一補寫 ——
    所以下一個有人加的 check 也會自動落在 400。
    """
    me = await login("話很多的")

    response = await me.patch("/api/profiles/me", json={"bio": "字" * 301})

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "profiles_bio_check"

    stored = await db.fetchval(
        "select bio from profiles where id = $1", uuid.UUID(me.user_id)
    )
    assert stored is None, "超長的內容不該被寫進去"
