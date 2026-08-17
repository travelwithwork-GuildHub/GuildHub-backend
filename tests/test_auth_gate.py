"""[P22] 未登入一律 401。規格書 §4.3、附錄 C。

    「所有資料庫存取經由 FastAPI，單一入口。權限在應用層以依賴注入強制，
      每個端點明確宣告存取主體。」

這個檔案不需要資料庫：get_current_user 在任何 SQL 之前就擋下來了。若哪天有
端點忘了宣告主體，它會在碰到資料庫時才失敗（沒有資料庫就是連線錯誤），
而不是回 401 —— 所以這裡也順便證明了「每個端點都真的掛了依賴」。

/api/login 不在名單裡，它本來就是公開的（附錄 B）。
"""

import uuid

import httpx
import pytest

from app.main import app

PROJECT = uuid.uuid4()
PROFILE = uuid.uuid4()

# 附錄 B 的全部端點，扣掉公開的 /api/login。附帶合法 body，讓唯一的失敗理由
# 是「沒登入」而不是「body 不合法」。
PROTECTED = [
    ("GET", "/api/me", None),
    ("GET", "/api/profiles", None),
    ("GET", f"/api/profiles/{PROFILE}", None),
    ("PATCH", "/api/profiles/me", {"display_name": "改名"}),
    ("POST", "/api/projects", {"title": "標題", "body": "內文"}),
    ("GET", "/api/projects", None),
    ("GET", f"/api/projects/{PROJECT}", None),
    ("POST", f"/api/projects/{PROJECT}/form-team", {"password": "pw"}),
    ("POST", f"/api/projects/{PROJECT}/close", None),
    ("POST", f"/api/projects/{PROJECT}/enter", {"password": "pw"}),
    ("GET", f"/api/projects/{PROJECT}/seats", None),
    ("POST", f"/api/projects/{PROJECT}/seats", {"seat_index": 0}),
    ("POST", "/api/messages", {"recipient_id": str(PROFILE), "body": "嗨"}),
    ("GET", "/api/messages", None),
    ("GET", "/api/rooms", None),
]


@pytest.mark.parametrize(
    "method,path,body", PROTECTED, ids=[f"{m} {p}" for m, p, _ in PROTECTED]
)
async def test_unauthenticated_requests_are_rejected(method, path, body):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 401, (
        f"{method} {path} 回了 {response.status_code}，未登入卻沒被擋下"
    )


async def test_login_is_public():
    """/api/login 不該要求登入 —— 它不會回 401（沒有資料庫時會是別的錯）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            response = await client.post("/api/login", json={"nickname": "新來的"})
        except Exception:
            return  # 沒有資料庫 —— 但至少不是被權限擋下的
    assert response.status_code != 401
