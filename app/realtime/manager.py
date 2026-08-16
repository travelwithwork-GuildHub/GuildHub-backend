"""ConnectionManager。規格書 §3.1：每個連線屬於且僅屬於一個 scene。

connect() 的順序是刻意的：先驗 scene 格式（還沒 accept，可以直接拒絕握手），
accept 之後才登記成員與 presence，最後才送 hello 與 snapshot。反過來做會讓
一條被拒絕的連線在名單裡留下殘影。
"""

import logging

from fastapi import WebSocket

from app.realtime import protocol
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry

log = logging.getLogger("guildhub.realtime")


class Connection:
    """一條 WS 連線。用預設的 identity hash —— 同一個人開兩個分頁是兩條連線。"""

    __slots__ = ("ws", "user_id", "name", "scene")

    def __init__(self, ws: WebSocket, user_id: str, name: str, scene: str):
        self.ws = ws
        self.user_id = user_id
        self.name = name
        self.scene = scene

    def __repr__(self):
        return f"Connection({self.name}@{self.scene})"


class ConnectionManager:
    def __init__(self, scenes: SceneRegistry, presence: PresenceStore):
        self.scenes = scenes
        self.presence = presence

    async def connect(
        self, ws: WebSocket, user_id: str, name: str, scene: str
    ) -> Connection:
        """[R16]。大廳連線不驗 token；房間的 room_token 由 [R31] 在此之前擋下。"""
        self.scenes.get_or_create(scene)  # 格式不合直接 ValueError，不 accept

        await ws.accept()
        conn = Connection(ws, user_id, name, scene)
        self.scenes.add_member(scene, conn)
        self.presence.join(user_id, name=name, scene=scene)

        await ws.send_text(protocol.hello(user_id))
        await ws.send_text(protocol.snapshot(self.presence.snapshot(scene)))
        return conn

    def forget(self, conn: Connection) -> None:
        """把連線從名單與 presence 移除。廣播離場是 [R24] 的事。"""
        self.scenes.remove_member(conn.scene, conn)
        if not any(
            other.user_id == conn.user_id
            for other in self.scenes.members(conn.scene)
        ):
            self.presence.clear(conn.user_id)
