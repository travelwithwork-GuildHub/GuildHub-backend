"""WS 訊息協定 v1。開工前準備 附錄 A 的程式化版本。

這是全案唯一需要手寫的介面文件（REST 有 OpenAPI 自動產生），因此這個檔案
就是交付 P3 的規格本身。改這裡等於改對外介面。
"""

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, StrictInt, TypeAdapter, ValidationError


class ProtocolError(Exception):
    """收到的訊息不符協定。呼叫端決定要丟棄還是回 err。"""


# ------------------------------------------------------------- client → server


class Move(BaseModel):
    """{"t":"move","x":120,"y":340,"f":2}

    x／y 用 StrictInt：附錄 A.2 規定整數像素，不送浮點。浮點會讓每個座標
    多出小數位元組，在 40 人 × 10 Hz 下是白花的頻寬。
    """

    t: Literal["move"]
    x: StrictInt
    y: StrictInt
    f: StrictInt = Field(ge=0, le=3)  # 0=下 1=左 2=右 3=上


class StatusIn(BaseModel):
    """{"t":"status","text":"趕工中"}

    12 字上限不在這裡擋 —— 那是 PresenceStore.set_status_text 的責任（[R13]），
    因為那裡才是狀態文字真正落腳的地方。
    """

    t: Literal["status"]
    text: str


class ChatIn(BaseModel):
    """{"t":"chat","body":"..."}"""

    t: Literal["chat"]
    body: str


ClientMessage = Union[Move, StatusIn, ChatIn]

_client = TypeAdapter(Annotated[ClientMessage, Field(discriminator="t")])


def parse_client(raw: str | bytes | dict) -> ClientMessage:
    """解析 client → server 訊息。任何不合協定的輸入一律 ProtocolError。

    hello / snapshot / pos / presence / err 不在這個 union 裡，因此客戶端
    無法偽造伺服器訊息 —— 會落在未知 t 的分支被擋下。
    """
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"非合法 JSON：{exc}") from exc
    else:
        data = raw

    if not isinstance(data, dict):
        raise ProtocolError("訊息必須是 JSON 物件")

    try:
        return _client.validate_python(data)
    except ValidationError as exc:
        raise ProtocolError(str(exc)) from exc
