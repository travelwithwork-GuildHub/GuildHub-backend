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
