"""BE-G03：遠端玩家的外觀不能一律是 0。

`manager.connect()` 呼叫 `presence.join()` 時沒有傳 avatar，`presence.py`
就吃預設值 `0` —— 前端把角色外觀做完，**別人看到的還是同一隻**。自己看自己
是對的（前端知道自己選了什麼），所以這個 bug 只有在兩個人同時在線上才看得見。

跟 BE-G02 是同一條斷掉的鏈：外觀存在名片上，世界只讀 session。
"""

import base64
import json

import itsdangerous
import pytest

from app import config
from app.main import _identify
from tools.fake_client import FakeClient


def session_of(client) -> dict:
    signer = itsdangerous.TimestampSigner(str(config.SESSION_SECRET))
    return json.loads(base64.b64decode(signer.unsign(client.cookies["session"])))


class _StubWS:
    def __init__(self, session: dict | None = None, headers: dict | None = None):
        self.scope = {"session": session} if session is not None else {}
        self.headers = headers or {}


async def test_login_writes_the_avatar_into_the_session(login):
    c = await login("阿凱")
    assert session_of(c)["avatar_id"] == 0  # 新名片的預設值


async def test_changing_the_avatar_updates_the_session(login):
    c = await login("阿凱")
    r = await c.patch("/api/profiles/me", json={"avatar_id": 3})
    assert r.status_code == 200, r.text

    assert session_of(c)["avatar_id"] == 3
    assert _identify(_StubWS(session_of(c)))[2] == 3


async def test_changing_other_fields_leaves_the_avatar_alone(login):
    c = await login("阿凱")
    await c.patch("/api/profiles/me", json={"bio": "路過"})
    assert session_of(c)["avatar_id"] == 0


@pytest.mark.parametrize(
    "ws",
    [_StubWS(), _StubWS(session={}), _StubWS(session={"user_id": "u1"})],
    ids=["匿名", "空 session", "舊 cookie 沒有 avatar_id 這個鍵"],
)
def test_zero_is_still_the_default_for_anonymous(ws):
    """0 本身沒有錯 —— 匿名訪客沒有名片，本來就沒有外觀。

    錯的是已登入而且選過外觀的人也拿到 0。
    """
    assert _identify(ws)[2] == 0


async def test_other_players_see_the_real_avatar(server):
    """BE-G03 本身：乙看到的甲，av 要是甲選的那一個。

    刻意從**別人**的 snapshot 看 —— 自己看自己一直都是對的（前端本來就知道
    自己選了什麼），所以只驗自己會漏掉整個 bug。
    """
    a = FakeClient("甲", port=server, session_user="11111111-1111-1111-1111-111111111111", avatar_id=5)
    b = FakeClient("乙", port=server, session_user="22222222-2222-2222-2222-222222222222", avatar_id=2)
    await a.connect()
    try:
        await b.connect()
        try:
            snapshot = await b.recv()
            assert snapshot["t"] == "snapshot"
            them = {p["id"]: p["av"] for p in snapshot["players"]}
            assert them[a.you] == 5, f"乙看到的甲 av={them[a.you]}，應該是 5"
            assert them[b.you] == 2
        finally:
            await b.close()
    finally:
        await a.close()


async def test_a_joining_player_is_broadcast_with_their_avatar(server):
    """後到的人是走 presence 廣播進來的，不是 snapshot —— 那條路徑也要帶 av。"""
    a = FakeClient("甲", port=server, session_user="33333333-3333-3333-3333-333333333333")
    await a.connect()
    try:
        await a.recv()  # 自己的 snapshot

        b = FakeClient("乙", port=server, session_user="44444444-4444-4444-4444-444444444444", avatar_id=7)
        await b.connect()
        try:
            msg = await a.recv()
            assert msg["t"] == "presence"
            joined = {p["id"]: p["av"] for p in msg["join"]}
            assert joined[b.you] == 7, f"甲收到的 join av={joined.get(b.you)}，應該是 7"
        finally:
            await b.close()
    finally:
        await a.close()
