"""ProjectService。規格書 §5.3 §5.4 §6.1 §6.2。

房間不是被認領的，是成軍的副作用（§6.1）。這個綁定消除了四種需要額外處理
的狀態：有房無專案、有專案無房、貼文已下架但房間沒開、房間釋放了但專案還在。
"""

import base64
import hashlib
import hmac
import os
import random
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app import db, room_token
from app.deps import get_current_user, require_owner
from app.models import (
    EnterIn,
    EnterOut,
    FormTeamIn,
    ProjectCreate,
    ProjectOut,
    ProjectStatus,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])

PAGE_SIZE = 20

# ⚠ 規格書未定義房間模板的數量（§6.1 只說「系統指派房間模板」）。
# 這個值是暫定的，需要 P1 裁決 —— 它同時也是美術要出幾套房間圖的依據。
ROOM_TEMPLATE_COUNT = 4

# 回應 model 不含 password_hash，這裡再列一次要撈的欄位，避免 select * 之後
# 有人不小心把整列丟進回應裡。
_COLUMNS = (
    "id, owner_id, title, body, needed_skills, status, room_template, "
    "seat_count, expires_at, updated_at"
)


# ------------------------------------------------------------------ 密碼雜湊
#
# 用 stdlib 的 scrypt，不引入 passlib／bcrypt：房間密碼是共享密碼，不是帳號
# 密碼（§6.2 不做成員制），為它加一個相依套件不划算。


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------- 查詢


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    payload: ProjectCreate,
    me: uuid.UUID = Depends(get_current_user),
) -> ProjectOut:
    """[P19]。expires_at 用資料庫預設值（now() + 7 days），不在應用層算。"""
    row = await db.pool().fetchrow(
        "insert into projects (owner_id, title, body, needed_skills, seat_count) "
        f"values ($1, $2, $3, $4, $5) returning {_COLUMNS}",
        me,
        payload.title,
        payload.body,
        payload.needed_skills,
        payload.seat_count,
    )
    return ProjectOut(**dict(row))


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    status: ProjectStatus = ProjectStatus.recruiting,
    page: int = 0,
    _me: uuid.UUID = Depends(get_current_user),
) -> list[ProjectOut]:
    """[P20] 任務看板。過期的不出現。

    只在查詢時過濾，不實作到期排程與提醒（守則 §3：貼文到期提醒、續期、
    自動下架排程已砍除）。「自動下架」因此是查詢的結果，不是背景工作 ——
    少了一個排程器，也少了一種會在半夜壞掉的東西。
    """
    rows = await db.pool().fetch(
        f"select {_COLUMNS} from projects "
        "where status = $1 and expires_at > now() "
        "order by updated_at desc limit $2 offset $3",
        status.value,
        PAGE_SIZE,
        max(page, 0) * PAGE_SIZE,
    )
    return [ProjectOut(**dict(r)) for r in rows]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    _me: uuid.UUID = Depends(get_current_user),
) -> ProjectOut:
    """[P21]。"""
    row = await db.pool().fetchrow(
        f"select {_COLUMNS} from projects where id = $1", project_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="專案不存在")
    return ProjectOut(**dict(row))


# ------------------------------------------------------------------ 生命週期


@router.post("/{project_id}/form-team", response_model=ProjectOut)
async def form_team(
    payload: FormTeamIn,
    project_id: uuid.UUID = Depends(require_owner),
) -> ProjectOut:
    """[P33] 成軍。§6.1：status／room_template／password_hash 同一個交易內寫完。

    分開寫的話，中間任何一步失敗都會留下 room_ready check 擋不住的中間狀態
    —— 不，其實 check 會擋住，這正是它存在的價值：即使這裡寫錯，資料庫也
    不會讓「成軍了但房間沒開」這件事發生。
    """
    template = random.randrange(ROOM_TEMPLATE_COUNT)
    try:
        row = await db.pool().fetchrow(
            "update projects set status = 'active', room_template = $2, "
            f"password_hash = $3, updated_at = now() where id = $1 returning {_COLUMNS}",
            project_id,
            template,
            hash_password(payload.password),
        )
    except asyncpg.CheckViolationError as exc:
        # room_ready 擋下的成軍請求 → 400（附錄 C）
        raise HTTPException(status_code=400, detail="房間未備妥，無法成軍") from exc
    return ProjectOut(**dict(row))


@router.post("/{project_id}/close", response_model=ProjectOut)
async def close_project(project_id: uuid.UUID = Depends(require_owner)) -> ProjectOut:
    """[P35] 結案。§6.1：座位全數釋放、門從走廊移除。

    ⚠ 這裡有一個規格內部的矛盾，需要 P1 裁決：
      · §4.1 說「專案結束時座位自動釋放」的機制是 on delete cascade
      · 但 cascade 只在「刪除專案列」時觸發，而 §6.1 的生命週期圖顯示
        closed 是專案保留下來的一個狀態（門移除，專案還在）
      · 任務表 [P34] 又註明「須由 DB cascade 達成，不得在應用層逐筆刪除」

    目前的作法：同一個交易內把 status 改成 closed，並以「一句 delete」釋放
    座位（集合操作，不是在 Python 裡逐筆迴圈）。若 P1 認定結案就該刪除專案
    列，改成 delete 即可，cascade 會自然接手。
    """
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "update projects set status = 'closed', updated_at = now() "
                f"where id = $1 returning {_COLUMNS}",
                project_id,
            )
            await conn.execute("delete from seats where project_id = $1", project_id)
    return ProjectOut(**dict(row))


@router.post("/{project_id}/enter", response_model=EnterOut)
async def enter_room(
    payload: EnterIn,
    request: Request,
    project_id: uuid.UUID,
    me: uuid.UUID = Depends(get_current_user),
) -> EnterOut:
    """[P37]。§6.2：共享密碼，不做成員制。密碼錯回 403。

    簽出的 token 有兩個用途：存進 session 給座位端點用，回傳給前端接在
    WebSocket 的 ?token= 上（附錄 A.1）。
    """
    stored = await db.pool().fetchval(
        "select password_hash from projects where id = $1", project_id
    )
    if stored is None:
        raise HTTPException(status_code=404, detail="專案不存在或房間尚未開啟")
    if not verify_password(payload.password, stored):
        raise HTTPException(status_code=403, detail="房間密碼錯誤")

    token = room_token.issue(str(project_id), str(me))
    tokens = dict(request.session.get("room_tokens") or {})
    tokens[str(project_id)] = token
    request.session["room_tokens"] = tokens
    return EnterOut(room_token=token)
