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

from pydantic import BaseModel, Field, model_validator


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
    """規格書 §9：匿名登入，暱稱即可，不走 OAuth。沒有密碼欄位。

    兩種模式，剛好給一個：

    - `nickname` —— 建立一張新名片
    - `resume_token` —— 拿回既有的名片。它就是 `ProfileOut.id`，登入時已經
      回給前端了，所以不必為它新增任何欄位或端點

    **`resume_token` 不是密碼。** 拿到它的人就是那張名片的人。它解的是「同一
    個人換裝置、或清掉 cookie 之後回得去」（BE-G01），不是「證明這個身分屬於
    我」。要後者得做帳號密碼，那會動到 sql/001_schema.sql 與規格書 §9，是另一
    個決定。

    - `login_id` + `password` —— 帳號密碼登入（L3，9/8 裁決）。這一組才是**驗證
      身分**：它證明這個身分屬於你，前兩種都不證明

    剛好給一組，多給或少給都是 422。刻意不做「都給就以某一邊為準」——
    那是在互相打架的意圖裡自己挑一邊信，而呼叫端不會知道被挑掉的是哪一個。

    三種模式並存不是折衷，是三種不同的入場方式：發表日現場走匿名（§9，不能
    卡在註冊），一般使用者走帳號密碼，換裝置的人走 resume_token。
    """

    nickname: str | None = None
    resume_token: uuid.UUID | None = None
    login_id: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def exactly_one_mode(self) -> "LoginIn":
        # login_id 與 password 先當成一組看：只給一半是「打錯了」，不是第四種模式
        if (self.login_id is None) != (self.password is None):
            raise ValueError("login_id 與 password 要一起給")

        given = sum(
            [
                self.nickname is not None,
                self.resume_token is not None,
                self.login_id is not None,
            ]
        )
        if given != 1:
            raise ValueError("nickname／resume_token／login_id+password 剛好給一組")
        return self


class RegisterIn(BaseModel):
    """註冊一個帳號（L3）。

    `login_id` 是帳號，`nickname` 是世界裡顯示的名字 —— 兩者刻意分開：
    `display_name` 沒有唯一約束而且改得動，拿它當帳號的話，改名就等於換帳號。

    `login_id` 的長度規則寫在 sql/001_schema.sql 的 check，這裡不重複
    （守則 §1 規則 3）。密碼長度是唯一的例外：**明文不會進資料庫**，
    SQL 無從驗起，所以它只能寫在這裡 —— 這不是「兩邊各寫一份」。
    """

    login_id: str
    password: str = Field(min_length=8)
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
