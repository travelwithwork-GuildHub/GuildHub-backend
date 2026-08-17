"""走廊門位。規格書 §5.2。

★ 全案唯一需要合併兩層的端點（附錄 B）。

走廊的門要顯示「專案名稱 + 當前在線人數」：前者在 PostgreSQL，後者在即時層
的記憶體。因為兩層跑在同一個 process，這只是一次函式呼叫 —— 但它是兩層唯一
的接觸點，值得單獨放一個檔案，日後出問題時第一個看它。

門的排列是一維的：x = index × 間距（§5.2），沒有版面演算法。前端照回傳順序
擺即可。預畫 12 個門位，用滿為止。
"""

import uuid

from fastapi import APIRouter, Depends

from app import db
from app.deps import get_current_user
from app.models import RoomDoorOut

router = APIRouter(prefix="/api", tags=["rooms"])

DOOR_SLOTS = 12  # §5.2：預畫 12 個門位，用滿為止


@router.get("/rooms", response_model=list[RoomDoorOut])
async def list_rooms(_me: uuid.UUID = Depends(get_current_user)) -> list[RoomDoorOut]:
    """成軍中（active）的專案就是走廊上的門。

    closed 的專案「門從走廊移除」（§6.1），因此這裡只查 active。
    """
    from app.main import scenes  # 延後 import：main 會 import 這個 router

    rows = await db.pool().fetch(
        "select id, title from projects where status = 'active' "
        "order by updated_at desc limit $1",
        DOOR_SLOTS,
    )
    return [
        RoomDoorOut(
            project_id=r["id"],
            title=r["title"],
            # 即時層的記憶體，不是資料庫 —— 在線人數從來沒有落地過
            online_count=scenes.online_count(f"room:{r['id']}"),
        )
        for r in rows
    ]
