"""AuthService。規格書 §9：匿名暱稱登入即可，不走 OAuth。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.deps import get_current_user
from app.models import LoginIn, ProfileOut

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login", response_model=ProfileOut)
async def login(payload: LoginIn, request: Request) -> ProfileOut:
    """[P12] login_anonymous(nickname)。

    沒有密碼欄位、沒有 OAuth（守則 §3）。登入即建立一張名片 —— 帳號與名片
    是同一個東西，profiles 就是使用者表。
    """
    user_id = uuid.uuid4()
    row = await db.pool().fetchrow(
        "insert into profiles (id, display_name) values ($1, $2) returning *",
        user_id,
        payload.nickname,
    )
    request.session["user_id"] = str(user_id)

    # 世界裡顯示的名字來自 session：main.py 的 _identify() 讀 session["name"]。
    # 在這一行之前沒有任何地方寫過那個鍵，所以每個人在 3D 世界裡都叫「訪客」
    # —— 而症狀看起來像前端把名字接錯了。
    #
    # 存 row 的值而不是 payload.nickname：名字的來源是資料庫，日後 schema
    # 若加了正規化或截斷，這裡會跟著對。
    request.session["name"] = row["display_name"]

    # 同一個理由：世界裡的外觀也來自 session。manager.connect() 不傳 avatar
    # 給 presence.join() 的話會吃到預設值 0 —— 前端把角色外觀做完，別人看到
    # 的還是同一隻。
    request.session["avatar_id"] = row["avatar_id"]

    return ProfileOut(**dict(row))


@router.get("/me", response_model=ProfileOut)
async def me(user_id: uuid.UUID = Depends(get_current_user)) -> ProfileOut:
    row = await db.pool().fetchrow("select * from profiles where id = $1", user_id)
    if row is None:
        # session 指向已不存在的名片（例如資料庫重建過）
        raise HTTPException(status_code=401, detail="未登入")
    return ProfileOut(**dict(row))
