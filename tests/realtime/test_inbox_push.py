"""[BE-G37] 新私訊的 WS 通知。前端 2026-09-19 的清單第 2.7 條。

⚠ **這一項有前置條件，見給前端的回覆文件 §2.7。**

`t:"inbox"` 是一種新的 server → client 訊息。前端的 WS client 如果對未知的
`t` 不是安靜忽略而是丟錯，那麼在他們接好這個訊息之前，**收到私訊的那個人的
3D 世界會在發表現場當掉**。前端回覆「會忽略」之前，這一項連同這個檔案都不
進 main。

## 為什麼不用真的 WebSocket

這個 repo 的 WS 測試（tests/realtime/）全部不碰資料庫，REST 測試全部不起
真 server —— 沒有任何一個測試同時要兩者。推送這件事剛好跨在中間，如果為它
發明「真 server ＋ 真資料庫」的組合，會多出兩個連線池與兩個事件迴圈要對齊，
而那種測試壞掉的時候沒有人分得出是功能壞了還是測試壞了。

所以拆成兩層，各自用這個 repo 已經有的方式測：

  1. `Broadcaster.send_to_user` 找不找得到人 —— 用 Conn 替身，跟
     tests/realtime/test_scenes.py 同一個做法，不需要 server 也不需要 db
  2. `POST /api/messages` 有沒有真的去推 —— 用 db fixture，把 broadcaster
     換成記錄呼叫的替身

兩層都綠，推送就是通的；中間那段「WebSocket 真的會把 bytes 送出去」由既有的
廣播測試涵蓋，不必再測一次。
"""

import json

import pytest

from app.realtime import protocol
from app.realtime.broadcaster import Broadcaster
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry


class FakeWS:
    """只需要 send_text —— broadcast 對連線的要求就這麼多。"""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


class Conn:
    """連線替身。跟 app.realtime.manager.Connection 一樣帶 user_id 與 ws。"""

    def __init__(self, user_id: str, scene: str = "lobby"):
        self.user_id = user_id
        self.scene = scene
        self.ws = FakeWS()


def make_broadcaster() -> tuple[Broadcaster, SceneRegistry]:
    scenes = SceneRegistry()
    return Broadcaster(scenes, PresenceStore()), scenes


def received(conn: Conn) -> list[dict]:
    return [json.loads(raw) for raw in conn.ws.sent]


# ------------------------------------------- 第一層：send_to_user 找得到人


async def test_send_to_user_reaches_someone_in_the_lobby():
    broadcaster, scenes = make_broadcaster()
    me = Conn("u1")
    scenes.add_member("lobby", me)

    await broadcaster.send_to_user("u1", protocol.inbox_out("m1", "u2"))

    assert received(me) == [{"t": "inbox", "message_id": "m1", "sender_id": "u2"}]


async def test_send_to_user_reaches_someone_inside_a_room():
    """收件匣不隔音（§3）：人在房間裡也要收得到自己的信。

    這是「兩層之間的第二個接觸點」存在的理由 —— 走廊那扇門（rooms.py）只看
    得到人數，看不到誰在哪裡。
    """
    broadcaster, scenes = make_broadcaster()
    me = Conn("u1", scene="room:p1")
    scenes.add_member("room:p1", me)

    await broadcaster.send_to_user("u1", protocol.inbox_out("m1", "u2"))

    assert len(received(me)) == 1


async def test_send_to_user_does_not_spray_the_scene():
    """只送給那個人。這是全案唯一一種「指定對象」的 WS 訊息。

    廣播禁止加入可見度判斷（[R21]）—— 這裡不是在 broadcast 裡加過濾，是另
    一條路：先挑出連線，再對那些連線送。
    """
    broadcaster, scenes = make_broadcaster()
    me, neighbour = Conn("u1"), Conn("u2")
    scenes.add_member("lobby", me)
    scenes.add_member("lobby", neighbour)

    await broadcaster.send_to_user("u1", protocol.inbox_out("m1", "u2"))

    assert len(received(me)) == 1
    assert received(neighbour) == [], "旁邊的人不得收到別人的收件匣通知"


async def test_send_to_user_reaches_every_tab_of_the_same_person():
    """同一個人開兩個分頁是兩條連線（Connection 的 docstring）。"""
    broadcaster, scenes = make_broadcaster()
    tab_a, tab_b = Conn("u1"), Conn("u1", scene="room:p1")
    scenes.add_member("lobby", tab_a)
    scenes.add_member("room:p1", tab_b)

    await broadcaster.send_to_user("u1", protocol.inbox_out("m1", "u2"))

    assert len(received(tab_a)) == 1
    assert len(received(tab_b)) == 1


async def test_send_to_an_offline_user_is_a_no_op():
    """不在線就什麼都不做 —— 沒有離線佇列（即時層不落地，絕對規則 5）。"""
    broadcaster, scenes = make_broadcaster()
    someone = Conn("u2")
    scenes.add_member("lobby", someone)

    await broadcaster.send_to_user("u1", protocol.inbox_out("m1", "u2"))

    assert received(someone) == []


async def test_a_dead_connection_does_not_break_the_send():
    """送不出去代表連線已死，交給 [R24] 清理 —— 跟 broadcast 同一個處理。"""
    broadcaster, scenes = make_broadcaster()
    dead, alive = Conn("u1"), Conn("u1")

    async def explode(_payload: str) -> None:
        raise RuntimeError("連線已死")

    dead.ws.send_text = explode
    scenes.add_member("lobby", dead)
    scenes.add_member("lobby", alive)

    await broadcaster.send_to_user("u1", protocol.inbox_out("m1", "u2"))

    assert len(received(alive)) == 1


def test_inbox_is_not_a_client_message():
    """客戶端偽造不了 —— 它不在 ClientMessage 的 union 裡。

    跟 hello / snapshot / pos / presence / err 同一個道理（protocol.py 的
    parse_client docstring）。
    """
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_client('{"t":"inbox","message_id":"m1","sender_id":"u2"}')


# --------------------------------- 第二層：POST /api/messages 真的會去推


@pytest.mark.db
class TestSendingAMessagePushes:
    """這一組要資料庫，但不需要真的 server。"""

    @pytest.fixture
    def pushes(self, monkeypatch) -> list[tuple[str, str]]:
        """把 main.broadcaster.send_to_user 換成記錄呼叫的替身。

        換的是 main 上的那一個物件 —— messages.py 是用
        `from app.main import broadcaster` 的延後 import 取它的（跟 rooms.py
        取 scenes 同一個寫法），所以這裡換掉就攔得到。
        """
        from app import main

        calls: list[tuple[str, str]] = []

        async def record(user_id: str, payload: str) -> None:
            calls.append((user_id, payload))

        # raising=False：方法還不存在時要讓測試在斷言上紅，而不是在 fixture
        # 裡 error —— error 看不出是哪一條期望沒被滿足。
        monkeypatch.setattr(main.broadcaster, "send_to_user", record, raising=False)
        return calls

    async def test_sending_a_message_pushes_to_the_recipient(self, db, login, pushes):
        sender = await login("寄信的")
        recipient = await login("收信的")

        sent = await sender.post(
            "/api/messages", json={"recipient_id": recipient.user_id, "body": "在嗎"}
        )
        assert sent.status_code == 201, sent.text

        assert len(pushes) == 1, f"推了 {len(pushes)} 次"
        user_id, payload = pushes[0]
        assert user_id == recipient.user_id, "推給收件人，不是寄件人"

        message = json.loads(payload)
        assert message["t"] == "inbox"
        assert message["message_id"] == sent.json()["id"]
        assert message["sender_id"] == sender.user_id

    async def test_the_push_carries_no_message_body(self, db, login, pushes):
        """只通知，不帶內容。

        §3.4：即時層不落地。把信的內容塞進 WS 廣播，等於讓「有紀錄的通道」
        的內容流進「沒有紀錄的通道」—— 兩者的保證不一樣，混在一起就講不清
        哪一份算數。收到通知之後請自己打 GET /api/messages。
        """
        sender = await login("寄信的")
        recipient = await login("收信的")

        await sender.post(
            "/api/messages",
            json={"recipient_id": recipient.user_id, "body": "這句話不該出現在 WS 上"},
        )

        assert "這句話不該出現在 WS 上" not in pushes[0][1]

    async def test_a_failed_push_does_not_fail_the_request(self, db, login, monkeypatch):
        """推送爆炸不得讓寄信失敗 —— 信已經寫進資料庫了。

        兩層的接觸點是單向的：REST 可以去戳即時層，但即時層的死活不能決定
        一封信有沒有寄成功。
        """
        from app import main

        # 這一條如果只用 raising=False 會在「根本沒推送」的情況下假綠，
        # 所以先明確要求方法存在。
        assert hasattr(main.broadcaster, "send_to_user"), "Broadcaster 還沒有 send_to_user"

        async def explode(_user_id: str, _payload: str) -> None:
            raise RuntimeError("即時層壞了")

        monkeypatch.setattr(main.broadcaster, "send_to_user", explode)

        sender = await login("寄信的")
        recipient = await login("收信的")

        sent = await sender.post(
            "/api/messages", json={"recipient_id": recipient.user_id, "body": "在嗎"}
        )

        assert sent.status_code == 201, sent.text
        assert await db.fetchval("select count(*) from messages") == 1

    async def test_a_rejected_message_pushes_nothing(self, db, login, pushes):
        """寄給自己被 no_self_send 擋下 —— 沒有信，就沒有通知。"""
        me = await login("自言自語的")

        response = await me.post(
            "/api/messages", json={"recipient_id": me.user_id, "body": "嗨"}
        )

        assert response.status_code == 400
        assert pushes == []
