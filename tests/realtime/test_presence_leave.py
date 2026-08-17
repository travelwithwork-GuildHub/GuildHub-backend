"""[R23] 斷線後其他 client 收到 presence.leave。規格書 §3.1。

在線名單靠進出場事件維護。沒有這個，離線的人會永遠站在原地 —— 而規格書 §7
明確排除了任何活動追蹤，所以「他還在不在」只能由連線本身回答。
"""

import asyncio
import uuid

from app import room_token
from tools.fake_client import FakeClient


async def test_presence_leave_is_broadcast_to_the_others(server):
    stayer = FakeClient("留下的", port=server)
    leaver = FakeClient("離開的", port=server)
    await stayer.connect()
    await leaver.connect()
    await asyncio.sleep(0.3)

    try:
        gone = leaver.you
        await leaver.close()

        async def watch():
            await stayer.drain(3.0)

        await watch()

        leaves = [
            uid
            for m in stayer.inbox
            if m["t"] == "presence"
            for uid in m.get("leave", [])
        ]
        assert gone in leaves, f"沒收到 {gone} 的 presence.leave，收到的是 {stayer.inbox}"
    finally:
        await stayer.close()


async def test_presence_join_is_broadcast_to_the_others(server):
    """附錄 A.3：進出場是同一種訊息的兩半。只有 leave 沒有 join，先到的人
    永遠看不到後到的人。"""
    early = FakeClient("先到的", port=server)
    await early.connect()
    await asyncio.sleep(0.3)
    early.inbox.clear()

    late = FakeClient("後到的", port=server)

    try:
        async def arrive():
            await asyncio.sleep(0.2)
            await late.connect()

        await asyncio.gather(arrive(), early.drain(2.0))

        joined = [
            p["id"]
            for m in early.inbox
            if m["t"] == "presence"
            for p in m.get("join", [])
        ]
        assert late.you in joined
    finally:
        await early.close()
        await late.close()


async def test_presence_leave_removes_the_player_from_later_snapshots(server):
    """離線的人不能出現在後來者的快照裡。"""
    leaver = FakeClient("路過的", port=server)
    await leaver.connect()
    gone = leaver.you
    await leaver.close()
    await asyncio.sleep(0.5)

    newcomer = FakeClient("後來的", port=server)
    await newcomer.connect()
    try:
        snapshot = await newcomer.recv()
        assert snapshot["t"] == "snapshot"
        assert gone not in [p["id"] for p in snapshot["players"]]
    finally:
        await newcomer.close()


async def test_presence_leave_does_not_cross_scenes(server):
    """規格書 §3.1：房間裡的人離線，大廳不該收到任何東西。"""
    in_lobby = FakeClient("大廳的", port=server)
    room_user = str(uuid.uuid4())
    in_room = FakeClient(
        "房間的",
        scene="room:spike",
        # §6.2：進房間要帶票，而且要是簽給自己的（[R31]）
        token=room_token.issue("spike", room_user),
        session_user=room_user,
        port=server,
    )
    await in_lobby.connect()
    await in_room.connect()
    await asyncio.sleep(0.3)
    in_lobby.inbox.clear()

    try:
        await in_room.close()
        await in_lobby.drain(1.5)

        assert not [m for m in in_lobby.inbox if m["t"] == "presence"]
    finally:
        await in_lobby.close()
