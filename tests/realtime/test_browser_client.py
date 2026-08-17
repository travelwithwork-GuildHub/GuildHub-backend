"""P3 的瀏覽器會怎麼連進來。

FakeClient 一律帶 x-fake-name header，但瀏覽器的 WebSocket API 不能設自訂
header —— 真正的前端只能靠 session cookie，而 session 要先 POST /api/login，
那需要資料庫（[D01]）。

所以在 [D01] 完成之前，P3 的連線是「完全沒有身分」的那一種。這個檔案確認
那條路徑真的能用：能握手、能收快照、能走動、能講話，只是名字都是「訪客」。

沒有這幾個測試的話，P3 明天接上來才會發現連不上，而我們的測試全綠。
"""

import asyncio
import json

import websockets

from app.realtime import protocol

LOBBY = "/ws?scene=lobby"


async def raw_connect(port: int):
    """不帶任何 header，就像瀏覽器那樣。"""
    return await websockets.connect(f"ws://127.0.0.1:{port}{LOBBY}")


async def test_browser_style_connection_completes_the_handshake(server):
    ws = await raw_connect(server)
    try:
        hello = json.loads(await ws.recv())
        assert hello["t"] == "hello"
        assert hello["hz"] == 10
        assert hello["you"]

        snapshot = json.loads(await ws.recv())
        assert snapshot["t"] == "snapshot"
        me = [p for p in snapshot["players"] if p["id"] == hello["you"]]
        assert me, "快照裡應該看得到自己"
        assert me[0]["name"] == "訪客", "沒有 session 時的預設暱稱"
        assert me[0]["st"] == ""  # §3.3：登入時為空白
    finally:
        await ws.close()


async def test_browser_style_client_can_move_and_be_seen(server):
    """P3 的三個函式（附錄 A.5）：onSnapshot / onPositions / sendMove。"""
    mover = await raw_connect(server)
    watcher = await raw_connect(server)
    try:
        for ws in (mover, watcher):
            await ws.recv()  # hello
            await ws.recv()  # snapshot

        await mover.send(protocol.Move(t="move", x=200, y=150, f=2).model_dump_json())

        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            msg = json.loads(await asyncio.wait_for(watcher.recv(), timeout=3.0))
            if msg["t"] == "pos":
                assert msg["p"][0][1:] == [200, 150, 2]
                return
        raise AssertionError("三秒內沒收到 pos")
    finally:
        await mover.close()
        await watcher.close()


async def test_browser_style_client_can_chat(server):
    speaker = await raw_connect(server)
    listener = await raw_connect(server)
    try:
        for ws in (speaker, listener):
            await ws.recv()
            await ws.recv()

        await speaker.send(protocol.ChatIn(t="chat", body="哈囉").model_dump_json())

        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            msg = json.loads(await asyncio.wait_for(listener.recv(), timeout=3.0))
            if msg["t"] == "chat":
                assert msg["body"] == "哈囉"
                assert msg["name"] == "訪客"
                return
        raise AssertionError("三秒內沒收到 chat")
    finally:
        await speaker.close()
        await listener.close()


async def test_browser_style_client_status_text_reaches_others(server):
    setter = await raw_connect(server)
    watcher = await raw_connect(server)
    try:
        for ws in (setter, watcher):
            await ws.recv()
            await ws.recv()

        await setter.send(protocol.StatusIn(t="status", text="趕工中").model_dump_json())

        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            msg = json.loads(await asyncio.wait_for(watcher.recv(), timeout=3.0))
            if msg["t"] == "status":
                assert msg["text"] == "趕工中"
                return
        raise AssertionError("三秒內沒收到 status")
    finally:
        await setter.close()
        await watcher.close()
