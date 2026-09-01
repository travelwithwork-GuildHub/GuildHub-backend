"""CORS。前端跑在自己的 dev port 時要能帶著 session cookie 打 API。

這個檔案不需要資料庫 —— 驗的是 middleware 的行為，不是端點的行為。

## 為什麼要有這組測試

CORS 壞掉的症狀全部發生在瀏覽器裡，後端的日誌什麼都看不到（preflight 被擋下時
請求根本不會到達端點）。前端只會看到「fetch failed」，然後兩邊各查半天。

所以三件事必須被釘住：
  1. 允許清單裡的來源拿得到 Access-Control-Allow-Origin
  2. 帶得了 cookie（allow_credentials），否則每個請求都是 401
  3. 回應的 origin 永遠是具體來源，不是 "*" —— 帶憑證時瀏覽器會拒收 "*"
"""

import importlib
import os

import httpx
import pytest

from app import config
from app.main import app

ALLOWED = config.CORS_ORIGINS[0]
FOREIGN = "http://evil.example.com"


def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_preflight_from_allowed_origin_is_accepted():
    """瀏覽器在真正的請求之前先問一次 OPTIONS。這一關過不了就沒有下一關。"""
    async with client() as c:
        r = await c.options(
            "/api/me",
            headers={
                "Origin": ALLOWED,
                "Access-Control-Request-Method": "GET",
            },
        )
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == ALLOWED


async def test_credentials_are_allowed():
    """身分走 session cookie。沒有這個 header，瀏覽器不會把 cookie 送出來，
    每個端點都會回 401。"""
    async with client() as c:
        r = await c.options(
            "/api/me",
            headers={"Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
        )
    assert r.headers["access-control-allow-credentials"] == "true"


async def test_actual_request_carries_cors_header():
    """preflight 過了不代表真正的請求也帶得回 header —— 兩者是分開的。

    這裡打的是未登入的 /api/me（401）。401 也必須帶 CORS header，否則前端
    的攔截器讀不到狀態碼，只會看到一個籠統的網路錯誤。
    """
    async with client() as c:
        r = await c.get("/api/me", headers={"Origin": ALLOWED})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == ALLOWED


async def test_unknown_origin_gets_no_cors_header():
    async with client() as c:
        r = await c.get("/health", headers={"Origin": FOREIGN})
    # 請求本身仍然成功（CORS 是瀏覽器端的規則，不是伺服器端的門禁），
    # 但沒有 header，瀏覽器就不會把結果交給頁面。
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


async def test_origin_header_is_never_wildcard():
    """allow_credentials=True 時回 "*" 會被瀏覽器拒收。

    這是最容易被「先讓它動起來」改壞的一行 —— 有人把 allow_origins 改成
    ["*"] 之後 preflight 看起來還是 200，但帶 cookie 的請求全部失敗。
    """
    async with client() as c:
        r = await c.get("/health", headers={"Origin": ALLOWED})
    assert r.headers.get("access-control-allow-origin") != "*"


def test_wildcard_in_config_fails_loudly():
    """CORS_ORIGINS="*" 必須在啟動時就炸掉，不是靜默地產生一個沒用的設定。"""
    original = os.environ.get("CORS_ORIGINS")
    os.environ["CORS_ORIGINS"] = "*"
    try:
        with pytest.raises(ValueError, match=r"\*"):
            importlib.reload(config)
    finally:
        if original is None:
            os.environ.pop("CORS_ORIGINS", None)
        else:
            os.environ["CORS_ORIGINS"] = original
        importlib.reload(config)  # 還原給其他測試用
