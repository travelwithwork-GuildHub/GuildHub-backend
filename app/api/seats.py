"""SeatService。規格書 §6.3。

★ [P40] 必須由 P1 親自 review。

守則 §4.1 點名這裡是全案最容易被善意寫錯的地方：

    # 錯 —— 兩人同時按下時兩個都會成功
    if not await seat_taken(project_id, seat_index):
        await insert_seat(...)

先查再寫之所以錯，不是因為機率低，是因為它在單機測試裡永遠是對的 ——
兩個請求要真的同時到達才會露出來，而那正是發表當天會發生的事。

正確的作法是讓資料庫的約束擋下：送出 INSERT，捕捉唯一鍵衝突，轉成 409。
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.deps import get_current_user, require_room_token
from app.models import SeatClaim, SeatOut

router = APIRouter(prefix="/api/projects", tags=["seats"])


@router.get("/{project_id}/seats", response_model=list[SeatOut])
async def list_seats(project_id: uuid.UUID = Depends(require_room_token)) -> list[SeatOut]:
    """[P42]。座位滿即房間滿（§6.3），不需另寫容量判斷。

    沒有釋放座位的端點 —— 已於 WBS v0.2 砍除（守則 §3）。
    """
    rows = await db.pool().fetch(
        "select seat_index, user_id, desk_template, claimed_at from seats "
        "where project_id = $1 order by seat_index",
        project_id,
    )
    return [SeatOut(**dict(r)) for r in rows]


@router.post("/{project_id}/seats", response_model=SeatOut, status_code=201)
async def claim_seat(
    payload: SeatClaim,
    project_id: uuid.UUID = Depends(require_room_token),
    me: uuid.UUID = Depends(get_current_user),
) -> SeatOut:
    """[P40] 認領座位。

    沒有 SELECT，沒有應用層的鎖。直接 INSERT，讓 seats 的兩個約束說話：
      · primary key (project_id, seat_index) → 一格只能一人
      · unique     (project_id, user_id)     → 一人只能一格
    兩者都轉成 409（附錄 C）。

    UniqueViolationError 同時涵蓋 PK 與 unique 衝突，因此用 constraint 名稱
    分辨是哪一種，好讓前端能給出不同的訊息。
    """
    try:
        row = await db.pool().fetchrow(
            "insert into seats (project_id, seat_index, user_id, desk_template) "
            "values ($1, $2, $3, $4) "
            "returning seat_index, user_id, desk_template, claimed_at",
            project_id,
            payload.seat_index,
            me,
            payload.desk_template,
        )
    except asyncpg.UniqueViolationError as exc:
        detail = (
            "你已經在這個房間有座位了"
            if "user_id" in (exc.constraint_name or "")
            else "這個座位已經有人了"
        )
        raise HTTPException(status_code=409, detail=detail) from exc
    except asyncpg.CheckViolationError as exc:
        # seat_in_range：0 ≤ seat_index < 8
        raise HTTPException(status_code=400, detail="座位編號超出範圍") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=404, detail="專案不存在") from exc

    return SeatOut(**dict(row))
