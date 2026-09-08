"""L3：帳號密碼登入（9/8 裁決）。

三種入場方式並存，這個檔案驗第三種：

- 匿名暱稱（規格書 §9，發表日現場進場走這條）
- `resume_token`（BE-G01，換裝置回得去）
- **`login_id` + `password`（這裡）—— 只有這一種在證明身分**

前兩種都不驗證身分：拿到 token 的人就是那個人。這一種才是。
"""

import base64
import json

import itsdangerous
import pytest

from app import config
from app.main import _identify

pytestmark = pytest.mark.db

ACCOUNT = {"login_id": "akai", "password": "guild1234", "nickname": "阿凱"}


def session_of(client) -> dict:
    signer = itsdangerous.TimestampSigner(str(config.SESSION_SECRET))
    return json.loads(base64.b64decode(signer.unsign(client.cookies["session"])))


class _StubWS:
    def __init__(self, session: dict | None = None):
        self.scope = {"session": session} if session is not None else {}
        self.headers = {}


# ---------------------------------------------------------------------- 註冊


async def test_register_creates_a_logged_in_profile(api):
    """註冊完就是登入狀態 —— 不要求使用者再登入一次。"""
    created = await api.post("/api/register", json=ACCOUNT)
    assert created.status_code == 200, created.text
    assert created.json()["display_name"] == "阿凱"

    me = await api.get("/api/me")
    assert me.status_code == 200
    assert me.json()["id"] == created.json()["id"]


async def test_register_never_returns_credentials(api):
    """ProfileOut 是人才看板上公開的東西，帳號與雜湊都不該在裡面。"""
    body = (await api.post("/api/register", json=ACCOUNT)).json()

    assert "login_id" not in body
    assert "password_hash" not in body
    assert "password" not in body


async def test_the_world_sees_the_registered_name(api):
    """第三條登入路徑一樣不能讓人變回「訪客」（BE-G02 的第三次）。"""
    await api.post("/api/register", json=ACCOUNT)

    assert _identify(_StubWS(session_of(api)))[1] == "阿凱"


async def test_duplicate_login_id_is_409(api, login):
    """撞名由 unique 擋，不是先查再寫（守則 §1 規則 4）。"""
    await api.post("/api/register", json=ACCOUNT)

    other = await login("路人")
    again = await other.post("/api/register", json=ACCOUNT | {"nickname": "冒充的"})

    assert again.status_code == 409
    assert again.json()["detail"] == "這個帳號已經有人用了"


async def test_a_short_password_is_422(api):
    """密碼長度是唯一寫在應用層的長度規則 —— 明文不進資料庫，SQL 驗不到。"""
    response = await api.post("/api/register", json=ACCOUNT | {"password": "guild"})

    assert response.status_code == 422


# ---------------------------------------------------------------------- 登入


async def test_login_with_password_returns_the_same_profile(api, login):
    """換一台電腦、沒有 cookie、也沒有 resume_token，只靠帳號密碼回來。"""
    registered = await api.post("/api/register", json=ACCOUNT)

    fresh = await login("先建一個乾淨的 client")  # 另一個 cookie jar
    back = await fresh.post(
        "/api/login", json={"login_id": "akai", "password": "guild1234"}
    )

    assert back.status_code == 200, back.text
    assert back.json()["id"] == registered.json()["id"]
    assert (await fresh.get("/api/me")).json()["id"] == registered.json()["id"]


@pytest.mark.parametrize(
    "credentials",
    [
        {"login_id": "akai", "password": "錯的密碼啦"},
        {"login_id": "沒有這個人", "password": "guild1234"},
    ],
    ids=["密碼錯", "帳號不存在"],
)
async def test_wrong_credentials_say_the_same_thing(api, credentials):
    """兩種失敗回同一個碼、同一句話。

    分開回等於免費送出一支帳號存在性查詢器 —— 那是「有沒有這個人」的洩漏。
    用 403 而不是 401：整合指南 §4 約定 401 導向登入、403 留在密碼框，
    使用者本來就在登入畫面上。
    """
    await api.post("/api/register", json=ACCOUNT)

    response = await api.post("/api/login", json=credentials)

    assert response.status_code == 403
    assert response.json()["detail"] == "帳號或密碼錯誤"


async def test_password_is_not_stored_in_plain_text(db, api):
    await api.post("/api/register", json=ACCOUNT)

    stored = await db.fetchval("select password_hash from profiles where login_id = 'akai'")

    assert stored.startswith("scrypt$")
    assert "guild1234" not in stored


# ------------------------------------------------------------ 三種模式的邊界


@pytest.mark.parametrize(
    "body",
    [
        {"login_id": "akai"},
        {"password": "guild1234"},
        {"nickname": "阿凱", "login_id": "akai", "password": "guild1234"},
        {"resume_token": "8ac0f1e4-0000-4000-8000-000000000000",
         "login_id": "akai", "password": "guild1234"},
    ],
    ids=["只給帳號", "只給密碼", "暱稱＋帳號密碼", "token＋帳號密碼"],
)
async def test_login_takes_exactly_one_mode(api, body):
    assert (await api.post("/api/login", json=body)).status_code == 422


async def test_anonymous_login_still_works(api):
    """發表日現場進場走這條（規格書 §9）。L3 是加上去的，不是取代它。"""
    anon = await api.post("/api/login", json={"nickname": "現場訪客"})

    assert anon.status_code == 200
    assert anon.json()["display_name"] == "現場訪客"


async def test_an_anonymous_profile_has_no_credentials(db, api):
    """匿名名片兩欄皆空 —— schema 的 check 綁成全有或全無。"""
    created = await api.post("/api/login", json={"nickname": "現場訪客"})

    row = await db.fetchrow(
        "select login_id, password_hash from profiles where id = $1",
        __import__("uuid").UUID(created.json()["id"]),
    )
    assert row["login_id"] is None and row["password_hash"] is None
