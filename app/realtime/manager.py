"""ConnectionManager。規格書 §3.1：每個連線屬於且僅屬於一個 scene。

connect() 的步驟順序是刻意的，每一步都有理由：

  1. 先驗 scene 格式 —— 還沒 accept，可以直接拒絕握手
  2. accept
  3. 登記 presence，然後「先廣播 join、再把自己加進 scene 成員」
     —— 這樣新來的人不會收到自己的 join，而不必在 broadcast 裡寫排除邏輯
        （守則：broadcast 禁止加入可見度判斷，[R21]）
  4. 最後才送 hello 與 snapshot，此時 presence 已含自己，快照才完整

反過來做會讓一條被拒絕的連線在名單裡留下殘影。
"""

import logging
import time

from fastapi import WebSocket

from app.realtime import protocol
from app.realtime.broadcaster import Broadcaster
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry

log = logging.getLogger("guildhub.realtime")


class Connection:
    """一條 WS 連線。用預設的 identity hash —— 同一個人開兩個分頁是兩條連線。"""

    __slots__ = ("ws", "user_id", "name", "scene", "connected_at")

    def __init__(self, ws: WebSocket, user_id: str, name: str, scene: str):
        self.ws = ws
        self.user_id = user_id
        self.name = name
        self.scene = scene
        self.connected_at = time.monotonic()

    def __repr__(self):
        return f"Connection({self.name}@{self.scene})"


class ConnectionManager:
    def __init__(
        self, scenes: SceneRegistry, presence: PresenceStore, broadcaster: Broadcaster
    ):
        self.scenes = scenes
        self.presence = presence
        self.broadcaster = broadcaster

    async def connect(
        self, ws: WebSocket, user_id: str, name: str, scene: str
    ) -> Connection:
        """[R16]。大廳連線不驗 token；房間的 room_token 由 [R31] 在此之前擋下。"""
        self.scenes.get_or_create(scene)  # 格式不合直接 ValueError，不 accept

        await ws.accept()
        conn = Connection(ws, user_id, name, scene)

        player = self.presence.join(user_id, name=name, scene=scene)
        # 此刻自己還不是 scene 成員，所以不會收到自己的 join
        await self.broadcaster.broadcast(
            scene, protocol.presence(join=[player.as_dict()], leave=[])
        )

        self.scenes.add_member(scene, conn)
        await ws.send_text(protocol.hello(user_id))
        await ws.send_text(protocol.snapshot(self.presence.snapshot(scene)))
        return conn

    async def disconnect(self, conn: Connection) -> None:
        """[R24]。移除連線、清 presence、廣播離線。

        先移除成員再廣播，離開的人自然收不到自己的 leave。
        """
        self.scenes.remove_member(conn.scene, conn)

        still_here = any(
            other.user_id == conn.user_id for other in self.scenes.members(conn.scene)
        )
        if still_here:
            return  # 同一人的另一條連線還在，不算離場

        self.presence.clear(conn.user_id)  # §3.3：離線即清空狀態文字
        await self.broadcaster.broadcast(
            conn.scene, protocol.presence(join=[], leave=[conn.user_id])
        )
