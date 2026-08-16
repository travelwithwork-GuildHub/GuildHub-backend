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


# ------------------------------------------------------------- server → client
#
# 這一段一律回傳「已序列化的字串」而不是 dict。理由是廣播時同一則訊息要送給
# 數十條連線，序列化一次即可；若回傳 dict，每條連線都會各自再 dump 一遍。

HZ = 10  # 規格書 §3.2。固定值，不做成可設定 —— 見 [R19]。

# 省封包：不留空白、中文不轉義（\uXXXX 會讓每個中文字從 3 bytes 變 6 bytes）
_SEP = (",", ":")


def _dumps(payload: dict) -> str:
    return json.dumps(payload, separators=_SEP, ensure_ascii=False)


def hello(user_id: str) -> str:
    """握手完成。附錄 A.3：連線建立時送一次。"""
    return _dumps({"t": "hello", "you": user_id, "hz": HZ})


def snapshot(players: list[dict]) -> str:
    """全場快照。進場時一次，之後只送差量。

    players 的元素形如
    {"id","name","av","x","y","f","st"} —— 由 PresenceStore.snapshot() 產生。
    """
    return _dumps({"t": "snapshot", "players": players})


def positions(moved: list[tuple[str, int, int, int]]) -> str:
    """位置差量。附錄 A.4：用陣列而非物件。

    ["<id>",120,340,2] 而不是 {"id":...,"x":...,"y":...,"f":...} —— 40 人 ×
    10 Hz 下，欄位名會被重複送 400 次／秒。

    注意：無人移動時整則訊息不送（[R20]），不是送空陣列。這個函式不負責
    那個判斷，Broadcaster 才是。
    """
    return _dumps({"t": "pos", "p": [list(entry) for entry in moved]})


def presence(join: list[dict], leave: list[str]) -> str:
    """進出場。join 的元素同 snapshot，leave 只需要 id。"""
    return _dumps({"t": "presence", "join": join, "leave": leave})


def status_out(user_id: str, text: str) -> str:
    """有人變更狀態文字。"""
    return _dumps({"t": "status", "id": user_id, "text": text})


def chat_out(user_id: str, name: str, body: str) -> str:
    """有人發言。純廣播，不落地（規格書 §3.4）。"""
    return _dumps({"t": "chat", "id": user_id, "name": name, "body": body})


def err(code: str, msg: str) -> str:
    return _dumps({"t": "err", "code": code, "msg": msg})
