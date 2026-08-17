"""Pydantic schemas —— API 的唯一真實來源。

方向永遠是（開工前準備 §3.1）：

    sql/001_schema.sql  →  models.py  →  OpenAPI（自動產生）  →  P2

不得反向手改任何一步。這條紀律成立的話，就永遠不需要 API 手冊。

## 為什麼這裡幾乎沒有長度驗證

看起來會很想在 display_name 上加 max_length=20、在 body 上加 max_length=2000
—— schema 裡就是這樣寫的。**不要加。**

任務表 [P15]：禁止在應用層重複實作長度檢查。[P14]：超長測試須斷言是資料庫
錯誤，不是 Pydantic 錯誤。理由是規格書附錄原則 4「不變式寫在資料庫」——
兩邊各寫一份，日後改了一邊就會出現「Pydantic 說可以、資料庫說不行」的裂縫，
而那時沒有人知道哪一份才算數。

型別（uuid、int、list[str]）仍然由 Pydantic 管，那是序列化的事，不是不變式。
"""

import datetime as dt
import enum
import uuid

from pydantic import BaseModel


class ProjectStatus(str, enum.Enum):
    """對應 sql 的 project_status enum。"""

    recruiting = "recruiting"
    active = "active"
    closed = "closed"


# ------------------------------------------------------------------ 個人名片


class ProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str
    avatar_id: int
    skills: list[str]
    hours_per_week: int | None
    bio: str | None
    updated_at: dt.datetime


class ProfileUpdate(BaseModel):
    """PATCH /api/profiles/me。未給的欄位不動。"""

    display_name: str | None = None
    avatar_id: int | None = None
    skills: list[str] | None = None
    hours_per_week: int | None = None
    bio: str | None = None


# ---------------------------------------------------------------------- 登入


class LoginIn(BaseModel):
    """規格書 §9：匿名登入，暱稱即可，不走 OAuth。沒有密碼欄位。"""

    nickname: str


# ------------------------------------------------------------------ 專案／房間


class ProjectCreate(BaseModel):
    title: str
    body: str
    needed_skills: list[str] = []
    seat_count: int = 4


class ProjectOut(BaseModel):
    """刻意不含 password_hash。房間密碼是共享密碼，任何情況下都不回給客戶端。"""

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    body: str
    needed_skills: list[str]
    status: ProjectStatus
    room_template: int | None
    seat_count: int
    expires_at: dt.datetime
    updated_at: dt.datetime


class FormTeamIn(BaseModel):
    """成軍。§6.1：系統指派房間模板、發起人設定密碼，同一個動作完成。"""

    password: str


class EnterIn(BaseModel):
    password: str


class EnterOut(BaseModel):
    """§6.2：驗證通過後取得 room token，WS 握手時用（附錄 A.1）。"""

    room_token: str


# ---------------------------------------------------------------------- 座位


class SeatOut(BaseModel):
    seat_index: int
    user_id: uuid.UUID
    desk_template: int
    claimed_at: dt.datetime


class SeatClaim(BaseModel):
    seat_index: int
    desk_template: int = 0


# -------------------------------------------------------------------- 站內信


class MessageCreate(BaseModel):
    recipient_id: uuid.UUID
    body: str


class MessageOut(BaseModel):
    """immutable（§4.2）—— 沒有對應的 update / delete model，因為沒有那些端點。"""

    id: uuid.UUID
    sender_id: uuid.UUID
    recipient_id: uuid.UUID
    body: str
    created_at: dt.datetime
    read_at: dt.datetime | None


# ------------------------------------------------------------------ 走廊門位


class RoomDoorOut(BaseModel):
    """全案唯一合併兩層的回應（附錄 B ★）。

    title 來自 PostgreSQL，online_count 來自即時層的記憶體。兩層跑在同一個
    process，所以這只是一次函式呼叫 —— 但它是兩層唯一的接觸點，日後出問題
    時第一個看它。
    """

    project_id: uuid.UUID
    title: str
    online_count: int
