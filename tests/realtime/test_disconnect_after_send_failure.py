"""[R24] 廣播送不出去之後的斷線，要走正常的斷線路徑，不是噴一整段 traceback。

2026-09-15 在 Railway 上跑 40 併發時抓到的：假客戶端同時關閉，後端日誌出現
12 次 `RuntimeError: WebSocket is not connected. Need to call "accept" first.`，
一次噴太多行，觸發 Railway 每秒 500 行的日誌上限，丟了 183 行。本機跑不出來，
因為本機的關閉很乾淨；中間隔著代理，才會出現「送到一半對方已經斷了」。

機制（Starlette 1.6 `WebSocket.send`）：

1. 廣播迴圈對一條正在關閉的連線 `send_text`，底層丟 OSError
2. Starlette 把 `application_state` 設成 DISCONNECTED，轉丟 `WebSocketDisconnect`
   —— 廣播迴圈接住了，這一步沒事
3. 但那條連線自己的接收迴圈還卡在 `receive_text()` 裡。它等到的下一則訊息
   如果是 client 在關閉前送出的 move，迴圈處理完再呼叫一次 `receive_text()`，
   這次拿到的是 **RuntimeError**，不是 `WebSocketDisconnect`

`finally` 裡的清理照樣會跑，所以功能沒壞；壞的是日誌。發表結束時現場觀眾同時
關頁面，就是同一個情境 —— 那時候被擠掉的可能是真正要看的那幾行。

這個測試直接驅動 ASGI app，不起真的 server：要重現的是「送出失敗」與「又收到
一則訊息」這兩件事的先後順序，用真的網路做不出穩定的順序。
"""

import asyncio
import json

from app import main


async def test_move_after_broadcast_send_failure_ends_as_a_normal_disconnect():
    inbox: asyncio.Queue = asyncio.Queue()
    sent: list[dict] = []
    greeted = asyncio.Event()
    broken = False

    async def receive():
        return await inbox.get()

    async def send(message):
        if broken and message["type"] == "websocket.send":
            raise OSError("peer already gone")  # 代理那頭已經斷了
        sent.append(message)
        if message.get("text") and json.loads(message["text"])["t"] == "snapshot":
            greeted.set()

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": "/ws",
        "raw_path": b"/ws",
        "query_string": b"scene=lobby",
        "root_path": "",
        "headers": [(b"x-fake-name", b"closing")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
        "subprotocols": [],
        "state": {},
    }

    await inbox.put({"type": "websocket.connect"})
    app_task = asyncio.create_task(main.app(scope, receive, send))
    await asyncio.wait_for(greeted.wait(), 5)

    [conn] = [c for c in main.scenes.members("lobby") if c.name == "closing"]

    try:
        # 1–2：廣播送不出去。Starlette 把這條連線標成已斷線，廣播迴圈接住例外
        broken = True
        await main.broadcaster.broadcast("lobby", json.dumps({"t": "noop"}))

        # 3：client 在關閉前送出的最後一則 move 這時才被讀到
        move = json.dumps({"t": "move", "x": 1, "y": 1, "f": 0})
        await inbox.put({"type": "websocket.receive", "text": move})

        # 修好之前，這裡會是 RuntimeError 從 app 裡冒出來
        await asyncio.wait_for(app_task, 5)
    finally:
        if not app_task.done():
            app_task.cancel()

    assert conn not in main.scenes.members("lobby"), "斷線清理沒有跑"
