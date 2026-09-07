"""AuthService。規格書 §9：匿名暱稱登入即可，不走 OAuth。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.deps import get_current_user
from app.models import LoginIn, ProfileOut

router = APIRouter(prefix="/api", tags=["auth"])


def _remember(request: Request, row) -> None:
    """把名片寫進 session —— 世界裡的身分全部從這裡來。

    寫成一個函式而不是在兩條路徑各寫一次，是因為 BE-G02 就是這樣發生的：
    `_identify()` 讀 session["name"]，而當時沒有任何地方寫過它，於是世界裡
    每個人都叫「訪客」。多一條登入路徑就多一次漏寫的機會，所以只留一份。

    存 row 的值而不是呼叫端傳進來的值：名字與外觀的來源是資料庫，日後 schema
    若加了正規化或截斷，這裡會跟著對。
    """
    request.session["user_id"] = str(row["id"])
    request.session["name"] = row["display_name"]

    # 世界裡的外觀也來自 session。manager.connect() 不傳 avatar 給
    # presence.join() 的話會吃到預設值 0 —— 前端把角色外觀做完，別人看到的
    # 還是同一隻。
    request.session["avatar_id"] = row["avatar_id"]


@router.post("/login", response_model=ProfileOut)
async def login(payload: LoginIn, request: Request) -> ProfileOut:
    """[P12] login_anonymous(nickname)，以及 BE-G01 的 resume。

    沒有密碼欄位、沒有 OAuth（守則 §3）。登入即建立一張名片 —— 帳號與名片
    是同一個東西，profiles 就是使用者表。

    給 `resume_token` 則是拿回既有的名片，不建新的。**它不是驗證身分**：
    拿到 token 的人就是那張名片的人。這解掉的是「清掉 cookie 或換一台電腦
    就再也回不去」，不是「證明這個身分屬於我」——後者要帳號密碼，那會動到
    schema 與規格書 §9，是另一個決定。

    端點數與路徑都沒變，所以附錄 B 的契約沒動；變的只有 request body 的形狀。
    """
    if payload.resume_token is not None:
        row = await db.pool().fetchrow(
            "select * from profiles where id = $1", payload.resume_token
        )
        if row is None:
            # 名片被刪了，或資料庫重建過。不要靜默改成建一張新的 —— 那會讓
            # 「我回來了」與「我是新來的」在前端長得一模一樣。
            raise HTTPException(status_code=404, detail="名片不存在")
    else:
        row = await db.pool().fetchrow(
            "insert into profiles (id, display_name) values ($1, $2) returning *",
            uuid.uuid4(),
            payload.nickname,
        )

    _remember(request, row)
    return ProfileOut(**dict(row))


@router.get("/me", response_model=ProfileOut)
async def me(user_id: uuid.UUID = Depends(get_current_user)) -> ProfileOut:
    row = await db.pool().fetchrow("select * from profiles where id = $1", user_id)
    if row is None:
        # session 指向已不存在的名片（例如資料庫重建過）
        raise HTTPException(status_code=401, detail="未登入")
    return ProfileOut(**dict(row))
