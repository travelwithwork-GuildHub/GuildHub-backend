"""[BE-G36] WS chat 的長度上限與每連線節流。規格書 §3.4、附錄 A.2。

前端 2026-09-19 的清單第 2.6 條：`ChatIn.body` 沒有長度限制（REST 的
messages.body 有 1–2000 的 check，WS 沒有），廣播也沒有節流 —— **洗版沒有
任何東西擋得住**。前端有送出節流，但那只擋得住守規矩的人。

## 為什麼是丟棄，不是回 err、不是關連線

附錄 A.2 已經寫著「超頻或不合法的訊息直接丟棄，不回錯」，`main.py` 對超過
12 字的狀態文字就是 `continue`。這裡照同一個約定。

不關連線是額外的決定：在發表現場把人踢下線，他的角色會從世界裡消失，
在場的每個人看到的是「系統壞了」，而不是「那個人被擋了」。

## 為什麼限制不寫在 protocol.py 的 ChatIn 上

跟 StatusIn 同一個理由（protocol.py 的註解）：12 字上限寫在
PresenceStore.set_status_text，因為那裡才是狀態文字真正落腳的地方。chat 是
純廣播不落地，所以它的落腳處是 Broadcaster.relay_chat。

這裡一律數真的收到幾則，不檢查函式有沒有被呼叫（跟 [R17] [R18] 同一個要求）。
"""

import asyncio

from tools.fake_client import FakeClient

CHAT_MAX_CHARS = 500
CHAT_RATE_PER_SECOND = 5


def chats(client) -> list[str]:
    return [m["body"] for m in client.inbox if m["t"] == "chat"]


async def test_chat_at_the_length_limit_is_broadcast(server):
    """500 字是上限本身，必須送得出去 —— 否則就是把界線畫錯一格。"""
    speaker = FakeClient("話多的", port=server)
    listener = FakeClient("聽的", port=server)
    await speaker.connect()
    await listener.connect()

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await speaker.send(speaker.chat_msg("字" * CHAT_MAX_CHARS))

        await asyncio.gather(speak(), listener.drain(1.5))

        assert len(chats(listener)) == 1
        assert len(chats(listener)[0]) == CHAT_MAX_CHARS
    finally:
        await speaker.close()
        await listener.close()


async def test_an_overlong_chat_is_dropped(server):
    """501 字不廣播。數的是別人收到幾則，不是送出的人收到什麼回應。"""
    speaker = FakeClient("洗版的", port=server)
    listener = FakeClient("聽的", port=server)
    await speaker.connect()
    await listener.connect()

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await speaker.send(speaker.chat_msg("字" * (CHAT_MAX_CHARS + 1)))

        await asyncio.gather(speak(), listener.drain(1.5))

        assert chats(listener) == [], f"超長訊息被廣播出去了：{len(chats(listener))} 則"
    finally:
        await speaker.close()
        await listener.close()


async def test_the_length_limit_counts_characters_not_bytes(server):
    """中文一個字算一個。

    用 len(bytes) 的話中文 167 個字就被擋下（一個字 3 bytes），而上面那條
    「500 字要過」的測試用的正是中文 —— 兩條合起來釘住「數的是字」。
    presence.py 的 STATUS_MAX_CHARS 註解裡踩過同一個坑。
    """
    speaker = FakeClient("說中文的", port=server)
    listener = FakeClient("聽的", port=server)
    await speaker.connect()
    await listener.connect()

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await speaker.send(speaker.chat_msg("中" * 400))

        await asyncio.gather(speak(), listener.drain(1.5))

        assert len(chats(listener)) == 1, "400 個中文字是 1200 bytes，但只有 400 字"
    finally:
        await speaker.close()
        await listener.close()


async def test_an_empty_chat_is_dropped(server):
    """空訊息不廣播 —— 它在畫面上是一個沒有內容的氣泡。"""
    speaker = FakeClient("按到 enter 的", port=server)
    listener = FakeClient("聽的", port=server)
    await speaker.connect()
    await listener.connect()

    try:
        async def speak():
            await asyncio.sleep(0.2)
            await speaker.send(speaker.chat_msg(""))

        await asyncio.gather(speak(), listener.drain(1.5))

        assert chats(listener) == []
    finally:
        await speaker.close()
        await listener.close()


async def test_chat_is_rate_limited_per_connection(server):
    """一秒內狂送 20 則，別人收到的不得超過 5 則。

    這一條才是真正擋洗版的東西：長度限制擋不住「改送 100 則短訊息」。
    """
    flooder = FakeClient("洗版的", port=server)
    listener = FakeClient("聽的", port=server)
    await flooder.connect()
    await listener.connect()

    try:
        async def flood():
            await asyncio.sleep(0.2)
            for i in range(20):
                await flooder.send(flooder.chat_msg(f"洗 {i}"))

        await asyncio.gather(flood(), listener.drain(1.2))

        received = len(chats(listener))
        assert received <= CHAT_RATE_PER_SECOND, f"一秒內放行了 {received} 則"
        assert received > 0, "全部擋掉就不是節流，是把功能關掉了"
    finally:
        await flooder.close()
        await listener.close()


async def test_the_budget_refills_in_the_next_second(server):
    """節流是每秒的額度，不是「一條連線總共只能講 5 句」。"""
    speaker = FakeClient("正常講話的", port=server)
    listener = FakeClient("聽的", port=server)
    await speaker.connect()
    await listener.connect()

    try:
        async def speak():
            await asyncio.sleep(0.2)
            for i in range(CHAT_RATE_PER_SECOND):
                await speaker.send(speaker.chat_msg(f"第一秒 {i}"))
            await asyncio.sleep(1.2)
            await speaker.send(speaker.chat_msg("下一秒"))

        await asyncio.gather(speak(), listener.drain(2.2))

        assert "下一秒" in chats(listener), "額度沒有回補"
    finally:
        await speaker.close()
        await listener.close()


async def test_a_blocked_sender_stays_connected(server):
    """被擋下來的人不會斷線，他的角色也不會從世界裡消失。

    這是刻意的取捨，寫在這裡免得日後有人「順手」改成關連線：在發表現場，
    有人被踢下線的畫面跟系統壞掉是一樣的。
    """
    offender = FakeClient("犯規的", port=server)
    listener = FakeClient("聽的", port=server)
    await offender.connect()
    await listener.connect()

    try:
        async def misbehave_then_behave():
            await asyncio.sleep(0.2)
            await offender.send(offender.chat_msg("字" * (CHAT_MAX_CHARS + 1)))
            for i in range(20):
                await offender.send(offender.chat_msg(f"洗 {i}"))
            await asyncio.sleep(1.3)
            await offender.send(offender.chat_msg("我乖了"))

        await asyncio.gather(misbehave_then_behave(), listener.drain(2.4))

        assert "我乖了" in chats(listener), "連線被關掉了，或送出端已經死了"
    finally:
        await offender.close()
        await listener.close()
