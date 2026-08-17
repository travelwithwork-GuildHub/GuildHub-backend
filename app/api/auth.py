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
    return ProfileOut(**dict(row))


@router.get("/me", response_model=ProfileOut)
async def me(user_id: uuid.UUID = Depends(get_current_user)) -> ProfileOut:
    row = await db.pool().fetchrow("select * from profiles where id = $1", user_id)
    if row is None:
        # session 指向已不存在的名片（例如資料庫重建過）
        raise HTTPException(status_code=401, detail="未登入")
    return ProfileOut(**dict(row))
