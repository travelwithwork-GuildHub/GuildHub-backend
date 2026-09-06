"""BE-G02：世界裡顯示的名字必須是登入的暱稱，不是「訪客」。

`app/main.py` 的 `_identify()` 從 `session["name"]` 取名字，而
`POST /api/login` 從來沒有寫過那個鍵 —— 於是 3D 世界裡每個人都叫「訪客」，
症狀看起來像前端把名字接錯了。

即時層的測試沒抓到，是因為 `tools/fake_client.py` 的 `session_cookie()`
自己把 name 簽進 cookie 裡。**測試替身比 production 正確**，所以那條路徑
一直是綠的。這個檔案刻意走真的登入，不碰那個替身。
"""

import base64
import json

import itsdangerous
import pytest

from app import config
from app.main import _identify


def session_of(client) -> dict:
    """把 client 手上的 session cookie 解回 dict。

    用 middleware 同一把金鑰驗簽，所以讀到的就是伺服器會讀到的那一份 ——
    不是我們自己另外組一個出來。
    """
    signer = itsdangerous.TimestampSigner(str(config.SESSION_SECRET))
    raw = signer.unsign(client.cookies["session"])
    return json.loads(base64.b64decode(raw))


class _StubWS:
    """`_identify()` 只用到 scope 與 headers 兩個東西。"""

    def __init__(self, session: dict | None = None, headers: dict | None = None):
        self.scope = {"session": session} if session is not None else {}
        self.headers = headers or {}


async def test_login_writes_the_display_name_into_the_session(login):
    c = await login("阿凱")
    assert session_of(c)["name"] == "阿凱"


async def test_the_world_sees_the_logged_in_name(login):
    """這是 BE-G02 本身：同一份 session 交給 _identify()，要拿到「阿凱」。"""
    c = await login("阿凱")
    user_id, name = _identify(_StubWS(session_of(c)))

    assert name == "阿凱"
    assert name != "訪客"
    assert user_id == c.user_id


async def test_renaming_updates_what_the_world_sees(login):
    """改名之後 session 沒跟著改的話，就是同一個 bug 的安靜版本。"""
    c = await login("阿凱")
    r = await c.patch("/api/profiles/me", json={"display_name": "阿凱二號"})
    assert r.status_code == 200, r.text

    assert session_of(c)["name"] == "阿凱二號"
    assert _identify(_StubWS(session_of(c)))[1] == "阿凱二號"


async def test_updating_other_fields_leaves_the_name_alone(login):
    """只改 bio 不該動到名字 —— 這一條擋的是「順手每次都覆寫」那種修法。"""
    c = await login("阿凱")
    r = await c.patch("/api/profiles/me", json={"bio": "路過"})
    assert r.status_code == 200, r.text

    assert session_of(c)["name"] == "阿凱"


@pytest.mark.parametrize(
    "ws, expected",
    [
        (_StubWS(), "訪客"),
        (_StubWS(session={}), "訪客"),
        (_StubWS(session={"user_id": "u1"}), "訪客"),
    ],
    ids=["沒有 session", "空 session", "舊 cookie 沒有 name 這個鍵"],
)
def test_visitor_is_still_the_fallback(ws, expected):
    """「訪客」本身沒有錯，錯的是登入過的人也拿到它。

    現場訪客與這次修好之前簽發的舊 cookie 都還是走這條路 —— 這一條是為了
    避免有人把 fallback 一起拿掉。
    """
    assert _identify(ws)[1] == expected
