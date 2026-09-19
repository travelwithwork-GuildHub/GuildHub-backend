"""FastAPI app。規格書 §2.2：同一 process 同時掛載 REST 與 WebSocket。"""

import contextlib
import logging
import uuid
from urllib.parse import unquote

import asyncpg
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocketState

from app import config, db, errors
from app.api import (
    auth,
    messages,
    profiles,
    project_resources,
    projects,
    rooms,
    seats,
)
from app.realtime import protocol
from app.realtime.broadcaster import Broadcaster
from app.realtime.manager import ConnectionManager
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry
from app.room_token import InvalidRoomToken

log = logging.getLogger("guildhub")

# 即時層的全部狀態。單一 process，因此這幾個物件就是「伺服器的記憶」——
# 規格書 §2.2：40 人的狀態全放記憶體對單一 Python process 是輕負載。
scenes = SceneRegistry()
presence = PresenceStore()
broadcaster = Broadcaster(scenes, presence)
manager = ConnectionManager(scenes, presence, broadcaster)


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
# same_site／https_only 由 COOKIE_CROSS_SITE 決定（見 config.py）。同源部署時
# 是 lax，前端在別的網域時必須是 none + Secure，否則 cookie 跨不過去。
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site=config.SESSION_COOKIE_SAME_SITE,
    https_only=config.SESSION_COOKIE_HTTPS_ONLY,
)

# CORS 在 Session 之後掛，因此包在更外層（add_middleware 是往外加的）。順序
# 有影響：preflight 的 OPTIONS 不帶 cookie，必須在碰到 session 之前就被回掉。
#
# allow_credentials=True 是必要的 —— 身分走 session cookie，沒有它瀏覽器不會
# 送 cookie，每個請求都會是 401。它同時使得 allow_origins 不能是 "*"（見
# config.py 的檢查）。
#
# 前後端同源部署時 CORS_ORIGINS 會是空的，這個 middleware 就不放行任何跨源
# 請求 —— 同源請求本來就不經過它，行為正確。
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [P23] 路由組裝。只掛附錄 B 列出的端點，一個不多 —— tests/test_contract.py
# 會擋下任何多出來的路由。
# ------------------------------------------------------------ [BE-G29] 錯誤
#
# 前端 2026-09-19 清單 1.4 與 1.5。兩個 handler 一起看才完整：
#
#   · 第一個把 code 加進**每一個** HTTPException 的回應 —— detail 不動，
#     所以前端現有的字串比對全部照舊，這是加法不是換法
#   · 第二個接住沒人處理的 asyncpg.CheckViolationError。在這之前它會一路噴到
#     ASGI 外面變成 500：21 字的暱稱登入、2001 字的站內信都是（前端回報的
#     是前者，後者順便修掉）
#
# 掛在這裡而不是各端點各寫一次 try/except：既有的顯式捕捉（no_self_send、
# room_ready、seat_in_range、唯一鍵衝突）全部保留，它們有自己的碼與文案。
# 這個 handler 只接漏網的 —— 所以**日後新增的任何 check 都不會再漏成 500**，
# 最壞的情況只是訊息不夠具體。


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    """回應多一個 code 欄位。沒有 code 的（例如 profiles 的 422「未知欄位」）
    就不加，不要硬湊一個出來 —— 422 的格式是 FastAPI 的，不是我們的。"""
    payload = {"detail": exc.detail}
    code = getattr(exc, "code", None)
    if code:
        payload["code"] = code
    return JSONResponse(payload, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(asyncpg.CheckViolationError)
async def check_violation_handler(_request: Request, exc: asyncpg.CheckViolationError):
    """資料庫 check 擋下的請求：400，code 就是 constraint 的名稱。

    刻意不是 422：422 在附錄 C 的約定是「請求本身不合法」（型別錯、欄位缺），
    由 FastAPI 判斷；這裡是「資料庫的不變式擋下來」，跟 room_ready 的 400
    同一類（附錄 C 最後一列）。

    長度規則仍然只寫在 sql/001_schema.sql 一份 —— 這裡只是把資料庫的答案
    翻譯成 HTTP，沒有在應用層重新實作任何一條規則（守則 §1 規則 1、[P15]）。
    """
    name = exc.constraint_name or "check_violation"
    detail = errors.CHECK_VIOLATION_MESSAGES.get(name, errors.GENERIC_CHECK_VIOLATION)
    log.info("資料庫 check 擋下請求：%s", name)
    return JSONResponse({"detail": detail, "code": name}, status_code=400)


app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(projects.router)
app.include_router(seats.router)
app.include_router(messages.router)
app.include_router(rooms.router)
app.include_router(project_resources.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _identify(ws: WebSocket) -> tuple[str, str, int]:
    """身分解析。附錄 A.1：身分來自登入時的 session cookie。

    沒有 session 時走匿名路徑 —— 規格書 §9：發表日開放現場進場，暱稱即可，
    不走 OAuth。[P12] login_anonymous 完成後 session 會是主要來源，這裡的
    fallback 則是假客戶端與現場訪客共用的入口。
    """
    session = ws.scope.get("session") or {}
    user_id = session.get("user_id")
    if user_id:
        # 「訪客」在這裡只剩兩種情況：cookie 是這次修好之前簽發的（沒有 name
        # 那個鍵），或名片被刪了。已登入的人走到這裡拿到「訪客」曾經是常態 ——
        # POST /api/login 從來沒有寫過 session["name"]，於是世界裡每個人都
        # 叫「訪客」。修在 app/api/auth.py。
        return user_id, session.get("name") or "訪客", session.get("avatar_id") or 0

    # 匿名訪客沒有名片，所以沒有外觀可言 —— 0 在這裡是「預設角色」，
    # 不是「漏傳」。差別在於已登入的人不該再走到這個 0。
    raw = ws.headers.get("x-fake-name")
    return str(uuid.uuid4()), (unquote(raw) if raw else "訪客"), 0


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, scene: str = "lobby", token: str | None = None):
    user_id, name, avatar_id = _identify(ws)

    try:
        conn = await manager.connect(ws, user_id, name, scene, token, avatar_id)
    except (ValueError, InvalidRoomToken) as exc:
        # 尚未 accept，close() 會讓握手直接以 HTTP 403 收場
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
                try:
                    presence.set_status_text(user_id, msg.text)
                except ValueError:
                    continue  # 超過 12 字（§3.3），丟棄不回錯
                await broadcaster.broadcast(
                    conn.scene, protocol.status_out(user_id, msg.text)
                )
            elif isinstance(msg, protocol.ChatIn):
                # §3.4：純廣播，不落地。這裡沒有任何寫入動作是刻意的。
                await broadcaster.relay_chat(conn.scene, user_id, name, msg.body)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 廣播端對這條連線送出失敗時，Starlette 把它標成 DISCONNECTED 並在
        # 廣播那一側丟 WebSocketDisconnect；這一側下一次 receive_text() 拿到的
        # 卻是 RuntimeError。那是同一個斷線，不是 bug —— 當成斷線收掉，否則
        # 同時關閉的人一多，traceback 會把平台的日誌額度灌爆
        # （tests/realtime/test_disconnect_after_send_failure.py）。
        # 只收這一種：連線還活著卻丟 RuntimeError 的話，那才是真的 bug，照丟。
        if ws.application_state != WebSocketState.DISCONNECTED:
            raise
    finally:
        await manager.disconnect(conn)
