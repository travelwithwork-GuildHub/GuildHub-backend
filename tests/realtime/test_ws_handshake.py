"""[R15] fake_client 連上並收到 hello。開工前準備 附錄 A.1。"""

from tools.fake_client import FakeClient


async def test_ws_handshake_receives_hello(server):
    client = FakeClient("阿凱", port=server)
    hello = await client.connect()
    try:
        assert hello["t"] == "hello"
        assert hello["hz"] == 10  # 附錄 A.3
        assert hello["you"]  # 伺服器指派的身分
    finally:
        await client.close()


async def test_ws_handshake_is_followed_by_a_snapshot(server):
    """附錄 A.3：進場時送一次全場快照，之後只送差量。"""
    client = FakeClient("小明", port=server)
    await client.connect()
    try:
        snapshot = await client.recv()
        assert snapshot["t"] == "snapshot"
        assert any(p["id"] == client.you for p in snapshot["players"])
    finally:
        await client.close()


async def test_ws_handshake_gives_each_connection_its_own_identity(server):
    a, b = FakeClient("甲", port=server), FakeClient("乙", port=server)
    await a.connect()
    await b.connect()
    try:
        assert a.you != b.you
    finally:
        await a.close()
        await b.close()


async def test_ws_handshake_rejects_an_illegal_scene(server):
    """[R07]：scene_id 只有 lobby 與 room:{id} 兩種格式。"""
    import websockets

    client = FakeClient("亂來的", scene="hall", port=server)
    try:
        await client.connect()
    except (websockets.ConnectionClosed, websockets.InvalidStatus, RuntimeError):
        return  # 被拒即為通過
    finally:
        await client.close()
    raise AssertionError("不合法的 scene 應該被拒絕")
