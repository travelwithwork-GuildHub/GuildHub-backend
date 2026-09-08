"""AuthService 與 ProfileService。規格書 §5.3 §9。"""

import uuid

import pytest

pytestmark = pytest.mark.db


# ------------------------------------------------------------------- [P11] 登入


async def test_auth_login_then_me_returns_myself(db, api):
    created = await api.post("/api/login", json={"nickname": "阿凱"})
    assert created.status_code == 200, created.text

    me = await api.get("/api/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "阿凱"
    assert me.json()["id"] == created.json()["id"]


async def test_auth_login_modes_are_frozen(db, api):
    """登入的輸入形狀是被鎖住的，要改必須有人裁決。

    這條測試原本叫 `test_auth_login_has_no_password_field`，守的是守則 §3
    「禁止密碼欄位」。**9/8 P1 裁決做 L3（帳號密碼登入），那條規則被正式
    翻案**，守則 §3 與 CLAUDE.md 已同步改寫 —— 所以這裡守的東西跟著換，
    不是被繞過。

    現在守的是三件仍然成立的事：

    1. 剛好三種模式，欄位集合精確 —— 多一個少一個都要有人來裁決
    2. **OAuth／第三方登入仍然不做**（守則 §3 那一列沒有被翻案）
    3. 匿名暱稱登入還在（規格書 §9：發表日現場進場不能卡在註冊）
    """
    from app.models import LoginIn

    assert set(LoginIn.model_fields) == {
        "nickname",
        "resume_token",
        "login_id",
        "password",
    }
    assert not [
        f for f in LoginIn.model_fields if "oauth" in f or "provider" in f
    ]

    anonymous = await api.post("/api/login", json={"nickname": "現場訪客"})
    assert anonymous.status_code == 200


async def test_auth_two_logins_are_two_people(db, login):
    a = await login("甲")
    b = await login("乙")
    assert a.user_id != b.user_id


async def test_auth_new_profile_starts_blank(db, api):
    created = await api.post("/api/login", json={"nickname": "新來的"})
    body = created.json()
    assert body["skills"] == []
    assert body["bio"] is None
    assert body["avatar_id"] == 0


# ----------------------------------------------------------- [P14] 名片往返


async def test_profile_round_trip(db, login):
    me = await login("阿凱")

    updated = await me.patch(
        "/api/profiles/me",
        json={"bio": "後端。相信約束要寫在資料庫裡。", "skills": ["Python", "SQL"],
              "hours_per_week": 20},
    )
    assert updated.status_code == 200, updated.text

    fetched = await me.get(f"/api/profiles/{me.user_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["bio"] == "後端。相信約束要寫在資料庫裡。"
    assert body["skills"] == ["Python", "SQL"]
    assert body["hours_per_week"] == 20


async def test_profile_partial_update_leaves_other_fields_alone(db, login):
    me = await login("阿凱")
    await me.patch("/api/profiles/me", json={"bio": "原本的自我介紹"})
    await me.patch("/api/profiles/me", json={"avatar_id": 3})

    body = (await me.get("/api/me")).json()
    assert body["bio"] == "原本的自我介紹"
    assert body["avatar_id"] == 3


async def test_profile_overlong_field_is_rejected_by_the_database(db, login):
    """[P14] 註記：超長測試須斷言是資料庫錯誤，不是 Pydantic 錯誤。

    models.py 刻意不重複實作長度檢查（[P15]），所以 bio 超過 300 字時擋下來
    的是 schema 裡的 check，不是 422。規則只有一份。
    """
    me = await login("話很多的")
    with pytest.raises(Exception):
        await me.patch("/api/profiles/me", json={"bio": "字" * 301})

    stored = await db.fetchval(
        "select bio from profiles where id = $1", uuid.UUID(me.user_id)
    )
    assert stored is None, "超長的內容不該被寫進去"


async def test_profile_length_rules_live_only_in_the_schema(db):
    """確認 check 真的在資料庫裡 —— 不然上面那個測試可能只是碰巧。"""
    constraints = await db.fetch(
        "select pg_get_constraintdef(oid) as def from pg_constraint "
        "where conrelid = 'profiles'::regclass and contype = 'c'"
    )
    defs = " ".join(c["def"] for c in constraints)
    assert "display_name" in defs and "bio" in defs


async def test_profile_get_unknown_id_is_404(db, login):
    me = await login("找人的")
    response = await me.get(f"/api/profiles/{uuid.uuid4()}")
    assert response.status_code == 404


# ----------------------------------------------------------- [P16] 翻頁邊界


async def test_profile_paging_first_page(db, login):
    viewer = await login("看的人")
    for i in range(5):
        await login(f"名片{i}")

    page0 = await viewer.get("/api/profiles?page=0")
    assert page0.status_code == 200
    assert len(page0.json()) >= 6  # 五張 + 自己


async def test_profile_paging_past_the_end_is_empty_not_an_error(db, login):
    """翻到底是正常操作，不是錯誤。"""
    viewer = await login("看的人")
    response = await viewer.get("/api/profiles?page=999")
    assert response.status_code == 200
    assert response.json() == []


async def test_profile_paging_negative_page_is_treated_as_first(db, login):
    viewer = await login("亂輸入的")
    response = await viewer.get("/api/profiles?page=-3")
    assert response.status_code == 200


async def test_profile_paging_does_not_repeat_rows(db, login):
    viewer = await login("看的人")
    for i in range(25):
        await login(f"名片{i}")

    page0 = {p["id"] for p in (await viewer.get("/api/profiles?page=0")).json()}
    page1 = {p["id"] for p in (await viewer.get("/api/profiles?page=1")).json()}
    assert page0 and page1
    assert not (page0 & page1), "兩頁出現重複的名片"
