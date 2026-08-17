"""[R28] lobby 的 chat 不會送到 room。規格書 §3.4。

大廳與房間機制相同：純廣播，不落地，無 scrollback。隔音由連線歸屬達成
（§3.1），不是靠可見度判斷。

設計意圖（§3.4）：沒有 scrollback，使用者不會把重要決議留在這裡，會自然
搬到會留存的地方。所以「重新整理後看到空白」是正確行為，不是缺陷。
"""

import asyncio

from app import room_token
from tools.fake_client import FakeClient


def chats(client) -> list[str]:
    return [m["body"] for m in client.inbox if m["t"] == "chat"]


def room_client(name: str, project_id: str, port: int) -> FakeClient:
    """進房間一定要帶 room_token（§6.2 / [R31]）。"""
    return FakeClient(
        name,
        scene=f"room:{project_id}",
        token=room_token.issue(project_id, name),
        port=port,
    )


async def test_chat_isolation_lobby_chat_never_reaches_a_room(server):
    in_lobby = FakeClient("大廳甲", port=server)
    also_lobby = FakeClient("大廳乙", port=server)
    in_room = room_client("房間丙", "chatiso", server)
    await in_lobby.connect()
    await also_lobby.connect()
    await in_room.connect()
    await asyncio.sleep(0.3)

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await in_lobby.send(in_lobby.chat_msg("大廳的悄悄話"))

        await asyncio.gather(
            speak(), also_lobby.drain(1.5), in_room.drain(1.5)
        )

        assert "大廳的悄悄話" in chats(also_lobby), "同場的人應該聽得到"
        assert chats(in_room) == [], "房間裡的人聽到了大廳的對話"
    finally:
        for c in (in_lobby, also_lobby, in_room):
            await c.close()


async def test_chat_isolation_room_chat_never_reaches_the_lobby(server):
    in_lobby = FakeClient("大廳的", port=server)
    room_a = room_client("房間甲", "chatiso2", server)
    room_b = room_client("房間乙", "chatiso2", server)
    await in_lobby.connect()
    await room_a.connect()
    await room_b.connect()
    await asyncio.sleep(0.3)

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await room_a.send(room_a.chat_msg("房間內的決議"))

        await asyncio.gather(speak(), room_b.drain(1.5), in_lobby.drain(1.5))

        assert "房間內的決議" in chats(room_b)
        assert chats(in_lobby) == []
    finally:
        for c in (in_lobby, room_a, room_b):
            await c.close()


async def test_chat_isolation_two_rooms_do_not_hear_each_other(server):
    a = room_client("p1 的人", "chatp1", server)
    b = room_client("p2 的人", "chatp2", server)
    await a.connect()
    await b.connect()
    await asyncio.sleep(0.3)

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await a.send(a.chat_msg("只有 p1 聽得到"))

        await asyncio.gather(speak(), b.drain(1.5))
        assert chats(b) == []
    finally:
        await a.close()
        await b.close()


async def test_chat_carries_the_speaker_identity(server):
    """附錄 A.3：{"t":"chat","id":...,"name":...,"body":...}"""
    speaker = FakeClient("阿凱", port=server)
    listener = FakeClient("聽的", port=server)
    await speaker.connect()
    await listener.connect()
    await asyncio.sleep(0.3)

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await speaker.send(speaker.chat_msg("先開會"))

        await asyncio.gather(speak(), listener.drain(1.5))

        said = [m for m in listener.inbox if m["t"] == "chat"]
        assert said, "沒收到任何 chat"
        assert said[0]["id"] == speaker.you
        assert said[0]["name"] == "阿凱"
        assert said[0]["body"] == "先開會"
    finally:
        await speaker.close()
        await listener.close()


async def test_chat_has_no_scrollback(server):
    """§3.4：重新進入後看到的是空白。這是設計意圖，不是缺陷。"""
    speaker = FakeClient("先講話的", port=server)
    await speaker.connect()
    await speaker.send(speaker.chat_msg("這句話不會被留下來"))
    await asyncio.sleep(0.5)

    latecomer = FakeClient("後來的", port=server)
    await latecomer.connect()
    try:
        await latecomer.drain(1.0)
        assert chats(latecomer) == [], "重新進入不該看到任何歷史對話"
    finally:
        await speaker.close()
        await latecomer.close()
