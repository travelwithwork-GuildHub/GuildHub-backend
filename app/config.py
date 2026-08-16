"""設定讀取。開工前準備 §2 第 2 項：只有三個值。"""

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
