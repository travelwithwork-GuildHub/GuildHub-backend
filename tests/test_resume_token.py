"""BE-G01：清掉 cookie 或換一台電腦之後，回得到同一張名片。

`POST /api/login` 原本是 `insert` 不是查詢 —— 每呼叫一次就是一個新的人，
舊名片變成孤兒。整合指南 §3.2 把這件事寫成「發表日的預期行為」，但它同時
也是前端標 🔴 Alarm 的那一條：世界裡沒有任何人回得來。

這裡驗的是最小的那個解法：`resume_token` 就是 `ProfileOut.id`，登入時已經
回給前端了。**端點沒有新增、schema 沒有動**，變的只有 request body 的形狀。

**它不是身分驗證。** 拿到 token 的人就是那張名片的人 —— 沒有任何一條測試
在這裡宣稱別的。要「證明這個身分屬於我」得做帳號密碼，那是另一個決定。
"""

import base64
import json
import uuid

import itsdangerous
import pytest

from app import config
from app.main import _identify

pytestmark = pytest.mark.db


def session_of(client) -> dict:
    """把 client 手上的 session cookie 解回 dict（同 tests/test_display_name.py）。"""
    signer = itsdangerous.TimestampSigner(str(config.SESSION_SECRET))
    raw = signer.unsign(client.cookies["session"])
    return json.loads(base64.b64decode(raw))


class _StubWS:
    """`_identify()` 只用到 scope 與 headers 兩個東西。"""

    def __init__(self, session: dict | None = None):
        self.scope = {"session": session} if session is not None else {}
        self.headers = {}


async def test_resume_returns_the_same_profile(login, api):
    """`api` 有自己的 cookie jar，所以這是真的「換一台電腦」。"""
    original = await login("阿凱")

    response = await api.post("/api/login", json={"resume_token": original.user_id})

    assert response.status_code == 200, response.text
    assert response.json()["id"] == original.user_id
    assert response.json()["display_name"] == "阿凱"


async def test_resume_does_not_create_another_profile(login, api):
    """回來的人不該在人才看板上多出一張名片 —— 那是這個修法最容易錯的地方。"""
    original = await login("阿凱")

    await api.post("/api/login", json={"resume_token": original.user_id})

    listed = await original.get("/api/profiles")
    assert [r["id"] for r in listed.json()] == [original.user_id]


async def test_the_resumed_session_is_logged_in(login, api):
    """恢復之後要真的是登入狀態，不是只拿到一份 JSON。"""
    original = await login("阿凱")
    await api.post("/api/login", json={"resume_token": original.user_id})

    me = await api.get("/api/me")

    assert me.status_code == 200
    assert me.json()["id"] == original.user_id


async def test_the_world_sees_the_resumed_name(login, api):
    """BE-G02 的第二條路徑：新增的登入路徑一樣不能讓人變回「訪客」。"""
    original = await login("阿凱")
    await api.post("/api/login", json={"resume_token": original.user_id})

    user_id, name, _avatar_id = _identify(_StubWS(session_of(api)))

    assert name == "阿凱"
    assert name != "訪客"
    assert user_id == original.user_id


async def test_resume_picks_up_the_current_name_and_avatar(login, api):
    """名字與外觀的來源是資料庫，不是登入當下那一次的輸入。"""
    original = await login("阿凱")
    patched = await original.patch(
        "/api/profiles/me", json={"display_name": "阿凱二號", "avatar_id": 3}
    )
    assert patched.status_code == 200, patched.text

    resumed = await api.post("/api/login", json={"resume_token": original.user_id})

    assert resumed.json()["display_name"] == "阿凱二號"
    assert session_of(api)["name"] == "阿凱二號"
    assert _identify(_StubWS(session_of(api)))[2] == 3


async def test_an_unknown_token_is_404(api):
    """名片被刪了或資料庫重建過。

    **不可以靜默改成建一張新的** —— 那會讓「我回來了」跟「我是新來的」在
    前端長得一模一樣，而使用者要等到發現朋友都不認得他了才知道出事。
    """
    response = await api.post("/api/login", json={"resume_token": str(uuid.uuid4())})

    assert response.status_code == 404
    assert response.json()["detail"] == "名片不存在"


async def test_a_malformed_token_is_422(api):
    response = await api.post("/api/login", json={"resume_token": "不是 uuid"})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"nickname": "阿凱", "resume_token": "8ac0f1e4-0000-4000-8000-000000000000"},
    ],
    ids=["兩個都沒給", "兩個都給了"],
)
async def test_exactly_one_mode(api, body):
    """兩個都給的時候不挑一邊信 —— 挑了，呼叫端不會知道被丟掉的是哪一個。"""
    response = await api.post("/api/login", json=body)

    assert response.status_code == 422


async def test_nickname_only_still_creates_a_new_profile(api):
    """回歸：舊的呼叫方式一個字都不用改，而且行為不變（整合指南 §3.2）。"""
    first = await api.post("/api/login", json={"nickname": "阿凱"})
    second = await api.post("/api/login", json={"nickname": "阿凱"})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
