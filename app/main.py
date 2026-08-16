"""FastAPI app。規格書 §2.2：同一 process 同時掛載 REST 與 WebSocket。"""

import contextlib
import logging
import uuid
from urllib.parse import unquote

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.middleware.sessions import SessionMiddleware

from app import config, db
from app.realtime import protocol
from app.realtime.broadcaster import Broadcaster
from app.realtime.manager import ConnectionManager
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry

log = logging.getLogger("guildhub")

# 即時層的全部狀態。單一 process，因此這幾個物件就是「伺服器的記憶」——
# 規格書 §2.2：40 人的狀態全放記憶體對單一 Python process 是輕負載。
scenes = SceneRegistry()
presence = PresenceStore()
manager = ConnectionManager(scenes, presence)
broadcaster = Broadcaster(scenes, presence)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 即時層不需要資料庫。連不上時仍要能啟動，否則軌 R 會被軌 P 擋住。
    try:
        await db.connect()
    except Exception as exc:  # noqa: BLE001
        log.warning("資料庫未連上，僅即時層可用：%s", exc)

    broadcaster.start()  # 10 Hz tick（[R19]）
    yield
    await broadcaster.stop()
    await db.disconnect()


app = FastAPI(title="GuildHub", version="0.1", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _identify(ws: WebSocket) -> tuple[str, str]:
    """身分解析。附錄 A.1：身分來自登入時的 session cookie。

    沒有 session 時走匿名路徑 —— 規格書 §9：發表日開放現場進場，暱稱即可，
    不走 OAuth。[P12] login_anonymous 完成後 session 會是主要來源，這裡的
    fallback 則是假客戶端與現場訪客共用的入口。
    """
    session = ws.scope.get("session") or {}
    user_id = session.get("user_id")
    if user_id:
        return user_id, session.get("name") or "訪客"

    raw = ws.headers.get("x-fake-name")
    return str(uuid.uuid4()), (unquote(raw) if raw else "訪客")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, scene: str = "lobby", token: str | None = None):
    user_id, name = _identify(ws)

    try:
        conn = await manager.connect(ws, user_id, name, scene)
    except ValueError as exc:
        log.info("拒絕握手：%s", exc)
        await ws.close(code=1008)
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = protocol.parse_client(raw)
            except protocol.ProtocolError:
                continue  # 附錄 A.2：超頻或不合法的訊息直接丟棄，不回錯

            if isinstance(msg, protocol.Move):
                presence.set_position(user_id, msg.x, msg.y, msg.f)
            elif isinstance(msg, protocol.StatusIn):
                with contextlib.suppress(ValueError):
                    presence.set_status_text(user_id, msg.text)
    except WebSocketDisconnect:
        pass
    finally:
        manager.forget(conn)
