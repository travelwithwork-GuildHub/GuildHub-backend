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
from fastapi import APIRouter, Depends

from app import db
from app.errors import ApiError
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

    ## 座位數上限為什麼寫成 INSERT ... SELECT ... WHERE

    規格書 §6.3 要求「房間人數上限 = 座位數」，但這條約束跨 projects 與 seats
    兩張表，PostgreSQL 的 check 寫不出來，所以由 P1 裁決改在這裡擋。

    看起來像是把「先查再寫」請回來了，其實不是 —— 關鍵在於查的是**哪張表**：

      · 危險的先查再寫，查的是 seats：「這格有沒有人坐？」
        兩個人同時查都會得到「沒有」，然後兩個都寫進去
      · 這裡查的是 projects.seat_count：「這個房間有沒有第 7 格？」
        這個答案跟誰在跟你搶完全無關，成軍之後 seat_count 也不再變動

    所以兩個不變式是正交的，加了容量檢查並沒有削弱競爭防護：
    同時搶 seat 1 的兩個人一樣會通過容量檢查、一樣在 PK 上撞、一樣只有一個
    成功。容量不足時則兩個都被擋，也是對的。

    即使如此仍然寫成單一句 INSERT ... SELECT 而不是「先 fetchval 再 insert」，
    有兩個理由：成功路徑少一次來回；以及不留下一段長得像正確寫法的先查再寫，
    讓日後讀這個檔案的人不會照著它去改別的地方。

    ## [BE-G32] status = 'active' 為什麼也在同一個 where 裡

    2026-09-19 之前，結案之後舊的 room token 仍然坐得到位：隊員 enter 拿到
    token → 發起人 close → 隊員用那張還在 8 小時 TTL 內的 token 再打這個
    端點，回 201，座位又長回來。/api/rooms 已經沒有這扇門，所以前端看不到，
    但資料在（前端清單 1.3）。

    前端提的作法是在 require_room_token 查一次 status。**沒有採用**：那個
    依賴同時守著 GET /seats 與 POST /seats，照做會讓「close 後 GET …/seats
    回 []」變成 403 —— 那是前端釘住的契約，而且讀一份空的座位表本來就無害。
    洞只在寫入。

    所以條件加在這句 where 裡，跟容量檢查同一個道理：查的是 projects 的狀態，
    那個答案跟誰在跟你搶無關。零額外查詢，也沒有新的「先查再寫」。
    """
    try:
        # 容量檢查（seat_index < projects.seat_count）寫在 insert 的 where 裡，
        # 不是先做一次 select 再決定要不要寫。見本檔案結尾的長註解。
        row = await db.pool().fetchrow(
            "insert into seats (project_id, seat_index, user_id, desk_template) "
            "select $1::uuid, $2::smallint, $3::uuid, $4::smallint "
            "from projects "
            "where id = $1::uuid and status = 'active' "
            "and $2::smallint < seat_count "
            "returning seat_index, user_id, desk_template, claimed_at",
            project_id,
            payload.seat_index,
            me,
            payload.desk_template,
        )
    except asyncpg.UniqueViolationError as exc:
        mine = "user_id" in (exc.constraint_name or "")
        raise ApiError(
            409,
            "already_seated" if mine else "seat_taken",
            "你已經在這個房間有座位了" if mine else "這個座位已經有人了",
        ) from exc
    except asyncpg.CheckViolationError as exc:
        # seat_in_range：0 ≤ seat_index < 8
        raise ApiError(400, "seat_out_of_range", "座位編號超出範圍") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise ApiError(404, "project_not_found", "專案不存在") from exc

    if row is None:
        # where 沒有命中，三種可能：專案不存在、房間不是 active、或座位編號
        # 超出這個房間的座位數。這一句只在請求已經失敗之後才跑，不在成功路徑
        # 上，也不參與任何競爭。
        state = await db.pool().fetchrow(
            "select status, seat_count from projects where id = $1", project_id
        )
        if state is None:
            raise ApiError(404, "project_not_found", "專案不存在")
        if state["status"] != "active":
            raise ApiError(409, "project_closed", "這個專案已經結案，不能再認領座位")
        seat_count = state["seat_count"]
        raise ApiError(
            400,
            "seat_beyond_seat_count",
            f"這個房間只有 {seat_count} 個座位（可選 0–{seat_count - 1}）",
        )

    return SeatOut(**dict(row))
