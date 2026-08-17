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

from app import room_token
from app.realtime import protocol
from app.room_token import InvalidRoomToken
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

    def authenticate_handshake(
        self, scene: str, token: str | None, user_id: str
    ) -> None:
        """[R31]。房間必須帶合法 room_token；大廳不驗（[R16]）。

        規格書 §6.2 與附錄 A.1：握手時驗證，失敗直接關閉連線。連上之後才驗
        是不行的 —— 那條連線在被踢掉之前已經進了成員名單，會收到一輪廣播，
        房間的隔音就破了。

        三個條件跟 REST 端的 deps.require_room_token 一致：簽章合法、房間
        對得上、持有人是自己。兩處驗法必須一樣 —— 同一種 token 在不同入口
        有不同的門檻，遲早會有人從鬆的那邊進來。

        持有人比對的副作用是「進房間一定要先登入」：匿名連線拿到的是隨機
        身分，不可能對得上任何一張票。這符合 §6.2 —— 票是 /enter 用房間
        密碼換來的，本來就不該有匿名進房這條路。
        """
        if not scene.startswith("room:"):
            return

        project_id = scene.split(":", 1)[1]
        claims = room_token.verify(token)  # 偽造／竄改／過期一律 InvalidRoomToken
        if claims.project_id != project_id:
            raise InvalidRoomToken("token 不屬於這個房間")
        if claims.user_id != user_id:
            raise InvalidRoomToken("token 不屬於你")

    async def connect(
        self, ws: WebSocket, user_id: str, name: str, scene: str, token: str | None = None
    ) -> Connection:
        """[R16]。驗證全部排在 accept 之前，被拒的連線不會留下任何痕跡。"""
        self.scenes.get_or_create(scene)  # 格式不合直接 ValueError，不 accept
        self.authenticate_handshake(scene, token, user_id)  # 房間才驗

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
