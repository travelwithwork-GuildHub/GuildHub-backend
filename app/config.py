"""設定讀取。開工前準備 §2 第 2 項：只有三個值（加上 CORS 之後是四個）。"""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://guildhub:guildhub@localhost:5432/guildhub"
)
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-session-secret-change-me")
ROOM_TOKEN_SECRET = os.getenv("ROOM_TOKEN_SECRET", "dev-room-token-secret-change-me")

# room token 有效期。規格書 §6.2：驗證通過後取得 room token 存於 session。
ROOM_TOKEN_TTL_SECONDS = 8 * 60 * 60

# 允許跨源請求的前端來源。逗號分隔，預設是 Vite 與 CRA／Next 的 dev port。
#
# 部署時前後端同一個 origin 的話，這個清單可以留空 —— 同源請求不經過 CORS。
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]

# 身分走 session cookie，所以 CORS 必須 allow_credentials=True，而瀏覽器規定
# 那種情況下 Access-Control-Allow-Origin 不得是 "*"。
#
# 寫成啟動就炸掉，不是靜默忽略：萬用字元 + 憑證的組合會被瀏覽器擋下，症狀是
# 「後端明明設了 CORS 但前端還是過不去」—— 那種錯要查很久。
if "*" in CORS_ORIGINS:
    raise ValueError(
        "CORS_ORIGINS 不得含 '*'：帶 cookie 的跨源請求必須逐一列出來源。"
        "前後端同源時請把這個值留空。"
    )

# ---------------------------------------------------------------- 跨站 cookie
#
# 前端部署在 Vercel／GitHub Pages／另一個網域上時，前後端是**跨站**（不只是
# 跨源）。瀏覽器對 SameSite=Lax 的 cookie 在跨站請求裡不會送出 —— 症狀是
# 登入回 200 拿得到名片，接著**每一個 API 都 401**，WebSocket 握手變成訪客。
# 看起來像後端壞了，其實 cookie 根本沒離開瀏覽器。
#
# SameSite=None 沒有 Secure 會被瀏覽器直接丟掉，所以兩個設定綁在同一個開關，
# 不給「只設一半」的機會 —— 跟 profiles 的 credentials_all_or_nothing 同一個
# 道理：半套的設定比沒設定更難查。
#
# 開這個開關的前提是**後端必須是 HTTPS**。
COOKIE_CROSS_SITE = os.getenv("COOKIE_CROSS_SITE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

SESSION_COOKIE_SAME_SITE = "none" if COOKIE_CROSS_SITE else "lax"
SESSION_COOKIE_HTTPS_ONLY = COOKIE_CROSS_SITE


def _is_local(origin: str) -> bool:
    return "localhost" in origin or "127.0.0.1" in origin


# 同樣是啟動就炸掉。這一條擋的是全案最貴的一種設定錯誤：CORS 設對了、部署也
# 成功了、登入也回 200 —— 然後每個請求 401，而兩邊都會先懷疑是對方的問題。
#
# 判準是「CORS 清單裡出現了非本機的來源」：那代表前端在另一個網域上，也就是
# 跨站。這時候還沒開 COOKIE_CROSS_SITE，就是漏了。
_remote_origins = [origin for origin in CORS_ORIGINS if not _is_local(origin)]
if _remote_origins and not COOKIE_CROSS_SITE:
    raise ValueError(
        f"CORS_ORIGINS 有非本機的來源 {_remote_origins}，但 COOKIE_CROSS_SITE "
        "沒有開。跨站請求瀏覽器不會送 SameSite=Lax 的 cookie —— 部署起來會是"
        "「登入成功，然後每個 API 都 401」。請設 COOKIE_CROSS_SITE=true，"
        "並確認後端是 HTTPS（SameSite=None 必須搭配 Secure）。"
    )
