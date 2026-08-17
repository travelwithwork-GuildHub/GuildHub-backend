"""RoomTokenService。規格書 §6.2：驗證通過後取得 room token 存於 session。

用 stdlib 的 hmac 而不是 JWT 套件：簽發與驗證都在同一個 process，共用同一把
secret，不需要跨服務的公鑰驗證，JWT 的那些欄位與演算法協商在這裡全是負擔
（附錄原則 5：不為未來預留擴充點）。

格式：base64url(project_id|user_id|expires_at) + "." + base64url(HMAC-SHA256)
"""

import base64
import hmac
import time
from dataclasses import dataclass
from hashlib import sha256

from app import config

class InvalidRoomToken(Exception):
    """偽造、竄改、過期、或根本不是 token。一律當成同一件事處理。"""


@dataclass(frozen=True)
class RoomTokenClaims:
    project_id: str
    user_id: str
    expires_at: float


def _secret() -> bytes:
    # 每次讀而不是啟動時快取：換了 secret，既有 token 立刻失效
    return config.ROOM_TOKEN_SECRET.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(_secret(), payload, sha256).digest())


def issue(project_id: str, user_id: str, ttl_seconds: float | None = None) -> str:
    """[P44]。同 process 共用 secret，不需跨服務簽發。"""
    if ttl_seconds is None:
        ttl_seconds = config.ROOM_TOKEN_TTL_SECONDS
    expires_at = time.time() + ttl_seconds
    payload = f"{project_id}|{user_id}|{expires_at:.0f}".encode()
    return f"{_b64(payload)}.{_sign(payload)}"


def verify(token: str | None) -> RoomTokenClaims:
    """[P45]。任何不對勁一律 InvalidRoomToken —— 不回報是哪裡不對。

    先比簽章再看內容：內容還沒被證明是我們簽的之前，不該拿它做任何判斷。
    """
    if not isinstance(token, str) or token.count(".") != 1:
        raise InvalidRoomToken("格式不正確")

    encoded, signature = token.split(".")
    try:
        payload = _unb64(encoded)
    except Exception as exc:  # noqa: BLE001
        raise InvalidRoomToken("payload 無法解碼") from exc

    # compare_digest：避免以比較耗時洩漏簽章前綴
    if not hmac.compare_digest(signature, _sign(payload)):
        raise InvalidRoomToken("簽章不符")

    try:
        project_id, user_id, expires_at = payload.decode().split("|")
        expiry = float(expires_at)
    except ValueError as exc:
        raise InvalidRoomToken("payload 內容不正確") from exc

    if expiry <= time.time():
        raise InvalidRoomToken("token 已過期")

    return RoomTokenClaims(project_id=project_id, user_id=user_id, expires_at=expiry)
