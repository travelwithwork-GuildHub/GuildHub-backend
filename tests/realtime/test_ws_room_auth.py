"""[R30] 無 room_token 連 room scene 被拒。規格書 §6.2。

附錄 A.1：房間連線必須帶 room_token，握手時驗證，失敗直接關閉連線。

「握手時」是重點 —— 不能先讓它連上再踢掉。連上之後才驗的話，那條連線在被
踢掉之前已經進了 scene 成員名單，會收到一輪廣播，房間的隔音就破了。
"""

import uuid

import websockets

from app import room_token
from tools.fake_client import FakeClient

REJECTED = (websockets.InvalidStatus, websockets.ConnectionClosed, RuntimeError)


async def _expect_rejected(client: FakeClient) -> None:
    try:
        await client.connect()
    except REJECTED:
        return
    finally:
        await client.close()
    raise AssertionError("這條連線本來就不該被接受")


async def test_ws_room_auth_rejects_a_room_without_a_token(server):
    await _expect_rejected(FakeClient("沒帶票的", scene="room:auth1", port=server))


async def test_ws_room_auth_rejects_a_garbage_token(server):
    await _expect_rejected(
        FakeClient("亂寫的", scene="room:auth1", token="not-a-token", port=server)
    )


async def test_ws_room_auth_rejects_a_token_for_another_project(server):
    """拿 A 房的票進 B 房。簽章是對的，但房間不對。"""
    tok = room_token.issue("auth-other", "user-1")
    await _expect_rejected(
        FakeClient("走錯房的", scene="room:auth1", token=tok, port=server)
    )


async def test_ws_room_auth_rejects_an_expired_token(server):
    tok = room_token.issue("auth1", "user-1", ttl_seconds=-1)
    await _expect_rejected(
        FakeClient("票過期的", scene="room:auth1", token=tok, port=server)
    )


async def test_ws_room_auth_accepts_a_valid_token(server):
    """票要簽給自己，而且連線要帶得出對應的身分。"""
    user_id = str(uuid.uuid4())
    client = FakeClient(
        "有票的",
        scene="room:auth1",
        token=room_token.issue("auth1", user_id),
        session_user=user_id,
        port=server,
    )
    hello = await client.connect()
    try:
        assert hello["t"] == "hello"
        assert hello["you"] == user_id  # 身分來自 session，不是隨機指派
    finally:
        await client.close()


async def test_ws_room_auth_rejects_someone_elses_token(server):
    """簽章合法、房間也對，但票不是簽給我的。

    這與 REST 端的 deps.require_room_token 是同一條規則 —— 同一種 token
    在兩個入口的門檻必須一樣，否則遲早有人從鬆的那邊進來。
    """
    me, someone_else = str(uuid.uuid4()), str(uuid.uuid4())
    await _expect_rejected(
        FakeClient(
            "借票的",
            scene="room:auth1",
            token=room_token.issue("auth1", someone_else),
            session_user=me,
            port=server,
        )
    )


async def test_ws_room_auth_rejects_anonymous_even_with_a_valid_token(server):
    """匿名連線進不了房間。

    這是加上持有人比對的直接後果：匿名身分是伺服器隨機指派的，對不上任何
    一張票。符合 §6.2 —— 票是拿房間密碼向 /enter 換來的，沒有匿名進房這條路。
    """
    await _expect_rejected(
        FakeClient(
            "沒登入的",
            scene="room:auth1",
            token=room_token.issue("auth1", str(uuid.uuid4())),
            port=server,  # 沒有 session_user
        )
    )


async def test_ws_room_auth_does_not_apply_to_the_lobby(server):
    """[R16]：大廳連線不驗 token。大廳是公共空間（§1）。"""
    client = FakeClient("大廳的", scene="lobby", port=server)
    hello = await client.connect()
    try:
        assert hello["t"] == "hello"
    finally:
        await client.close()


async def test_ws_room_auth_rejected_connection_never_joins_the_scene(server):
    """被拒的連線不得留下任何痕跡 —— 這是「握手時驗證」的實際意義。"""
    from app.main import scenes

    before = scenes.online_count("room:auth2")
    await _expect_rejected(FakeClient("沒票的", scene="room:auth2", port=server))
    assert scenes.online_count("room:auth2") == before
