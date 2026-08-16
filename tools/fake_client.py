"""[R04] 單一 WS 假客戶端。規格書 §9：假使用者必須會動。

這個類別刻意很笨 —— 它只會「組訊息、送、收、計數」。移動路線、狀態輪換等
驅動邏輯全部在 run_swarm.py，不在這裡（任務表 [R04]：禁止加入任何遊戲邏輯）。

不需要 server 也能 import 並組出合法訊息，因此它同時是協定的可執行文件。
"""

import asyncio
import json
from collections import Counter
from urllib.parse import quote

import websockets

from app.realtime import protocol


class FakeClient:
    def __init__(
        self,
        name: str,
        scene: str = "lobby",
        token: str | None = None,
        host: str = "127.0.0.1",
        port: int = 8000,
    ):
        self.name = name
        self.scene = scene
        self.token = token
        self.host = host
        self.port = port

        self.ws: websockets.ClientConnection | None = None
        self.you: str | None = None  # 握手後由 hello 填入
        self.counts: Counter = Counter()  # 依訊息型別計數，供節流驗收使用
        self.inbox: list[dict] = []

    # ---------------------------------------------------------- 組訊息（免 server）
    #
    # 一律經過 protocol 的 model 建構，因此組不出不合法的訊息 —— 若協定改了
    # 而這裡沒跟上，這幾個函式會直接拋錯，不會安靜地送出壞封包。

    def move_msg(self, x: int, y: int, f: int) -> str:
        return protocol.Move(t="move", x=x, y=y, f=f).model_dump_json()

    def status_msg(self, text: str) -> str:
        return protocol.StatusIn(t="status", text=text).model_dump_json()

    def chat_msg(self, body: str) -> str:
        return protocol.ChatIn(t="chat", body=body).model_dump_json()

    # ---------------------------------------------------------------------- 連線

    @property
    def url(self) -> str:
        url = f"ws://{self.host}:{self.port}/ws?scene={self.scene}"
        if self.token:
            url += f"&token={self.token}"
        return url

    async def connect(self, open_timeout: float = 10.0) -> dict:
        """連上並等 hello。回傳 hello 訊息本身。"""
        self.ws = await websockets.connect(
            self.url,
            open_timeout=open_timeout,
            # HTTP header 只吃 latin-1，中文暱稱必須先 percent-encode
            additional_headers={"x-fake-name": quote(self.name)},
        )
        hello = await self.recv()
        if hello.get("t") != "hello":
            raise RuntimeError(f"握手未收到 hello，收到：{hello}")
        self.you = hello["you"]
        return hello

    async def send(self, raw: str) -> None:
        await self.ws.send(raw)

    async def recv(self, timeout: float | None = 5.0) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        self.counts[msg.get("t", "?")] += 1
        self.inbox.append(msg)
        return msg

    async def drain(self, seconds: float) -> None:
        """收滿指定秒數並計數。逾時即結束，不視為錯誤 —— 靜止時本來就沒封包。"""
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                await self.recv(timeout=remaining)
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                return

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()
