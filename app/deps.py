"""FastAPI 依賴注入。規格書 §4.3：權限在應用層以依賴注入強制，每個端點明確
宣告存取主體。

刻意不用全域中介層把身分隱式帶入（任務表 [P13]）—— 隱式帶入會讓「這個端點
到底檢查了沒」變成要看中介層設定才知道的事，而那正是權限漏洞的溫床。
"""

import uuid

from fastapi import Depends, HTTPException, Path, Request

from app import db, room_token


async def get_current_user(request: Request) -> uuid.UUID:
    """[P13]。未登入回 401。"""
    raw = request.session.get("user_id")
    if not raw:
        raise HTTPException(status_code=401, detail="未登入")
    return uuid.UUID(raw)


async def require_owner(
    project_id: uuid.UUID = Path(...),
    me: uuid.UUID = Depends(get_current_user),
) -> uuid.UUID:
    """[P31]。非發起人操作 owner-only 端點回 403；專案不存在回 404。

    §6.2：不做成員制，只有發起人與其他人兩種身分。
    """
    owner_id = await db.pool().fetchval(
        "select owner_id from projects where id = $1", project_id
    )
    if owner_id is None:
        raise HTTPException(status_code=404, detail="專案不存在")
    if owner_id != me:
        raise HTTPException(status_code=403, detail="只有發起人可以做這件事")
    return project_id


async def require_room_token(
    request: Request,
    project_id: uuid.UUID = Path(...),
    me: uuid.UUID = Depends(get_current_user),
) -> uuid.UUID:
    """座位端點的門禁。§6.2：驗證通過後取得 room token 存於 session。

    token 由 POST /api/projects/{id}/enter 簽發。這裡只驗，不簽。
    """
    token = (request.session.get("room_tokens") or {}).get(str(project_id))
    try:
        claims = room_token.verify(token)
    except room_token.InvalidRoomToken as exc:
        raise HTTPException(status_code=403, detail="尚未通過房間密碼驗證") from exc

    if claims.project_id != str(project_id):
        raise HTTPException(status_code=403, detail="token 不屬於這個房間")

    # token 裡就帶著簽發對象，比一下沒有代價。
    #
    # 目前沒有路徑走得到這個分支 —— token 只由 /enter 簽發進自己的 session，
    # 拿到的一定是自己的。它防的是未來：哪天有人加了「從 header 或 body 收
    # room token」的端點，少了這一行就會變成可以借用別人的票。
    if claims.user_id != str(me):
        raise HTTPException(status_code=403, detail="token 不屬於你")

    return project_id
