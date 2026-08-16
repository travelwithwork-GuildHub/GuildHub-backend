"""[R17] [R18] 位置廣播節流。規格書 §3.2：節流是硬性要求。

任務表對這兩個測試的註記是「必須實際計數封包，不得只檢查函式有無被呼叫」。
因此這裡一律用真的 WebSocket 連線，數真的收到幾則 —— 直覺寫法（每幀送）會
讓訊息量變六倍並開始丟包，而那種錯誤只有數封包才看得出來。
"""

import asyncio
import time

from tools.fake_client import FakeClient

HZ = 10  # 附錄 A.3


async def test_throttle_idle_produces_zero_pos_packets(server):
    """靜止 3 秒內 pos 封包數為 0。

    §3.2：靜止時封包數必須為 0 —— 不是「送空陣列」，是整則訊息不送。
    """
    still = FakeClient("靜止的", port=server)
    company = FakeClient("陪站的", port=server)
    await still.connect()
    await company.connect()

    try:
        await asyncio.gather(still.drain(3.0), company.drain(3.0))
        assert still.counts["pos"] == 0, f"靜止卻收到 {still.counts['pos']} 則 pos"
        assert company.counts["pos"] == 0

        # 上面那個 0 必須是「節流的結果」，不能是「根本沒有廣播迴圈」。
        # 沒有這一段的話，[R19] 還沒實作時這個測試會假綠。
        async def move_once():
            await asyncio.sleep(0.2)
            await company.send(company.move_msg(11, 22, 3))

        await asyncio.gather(move_once(), still.drain(1.5))
        assert still.counts["pos"] > 0, "靜止時 0、一動也還是 0 —— tick 迴圈沒在跑"
    finally:
        await still.close()
        await company.close()


async def test_throttle_moving_stays_at_or_below_10_hz(server):
    """移動中每秒 pos 封包數 ≤ 10。

    客戶端故意以 30 Hz 送 —— 附錄 A.2：伺服器不信任客戶端的節流，自己以
    tick 為準。所以送再快，收到的也只能是 10 Hz。
    """
    mover = FakeClient("動的", port=server)
    watcher = FakeClient("看的", port=server)
    await mover.connect()
    await watcher.connect()

    seconds = 3.0

    async def spam_moves():
        deadline = time.monotonic() + seconds
        x = 0
        while time.monotonic() < deadline:
            x = (x + 7) % 600
            await mover.send(mover.move_msg(x, 100, 2))
            await asyncio.sleep(1 / 30)  # 三倍超頻

    try:
        await asyncio.gather(spam_moves(), watcher.drain(seconds))

        pos = watcher.counts["pos"]
        assert pos > 0, "有人在動，卻一則 pos 都沒收到"
        # 容許 1 則的邊界誤差（計時起訖不會剛好對齊 tick）
        assert pos <= HZ * seconds + 1, f"每秒 {pos / seconds:.1f} 則，超過 {HZ} Hz"
    finally:
        await mover.close()
        await watcher.close()


async def test_throttle_moving_pos_payload_uses_arrays(server):
    """附錄 A.4：位置差量用陣列而非物件。這裡驗的是線路上真正的樣子。"""
    mover = FakeClient("動的", port=server)
    watcher = FakeClient("看的", port=server)
    await mover.connect()
    await watcher.connect()

    try:
        async def move_once():
            await asyncio.sleep(0.15)
            await mover.send(mover.move_msg(42, 84, 1))

        await asyncio.gather(move_once(), watcher.drain(1.5))

        positions = [m for m in watcher.inbox if m["t"] == "pos"]
        assert positions, "沒收到任何 pos"
        entry = positions[0]["p"][0]
        assert isinstance(entry, list), f"位置差量必須是陣列，收到 {type(entry)}"
        assert len(entry) == 4  # [id, x, y, f]
        assert isinstance(entry[1], int) and isinstance(entry[2], int)
    finally:
        await mover.close()
        await watcher.close()


async def test_throttle_stops_again_once_movement_stops(server):
    """動完之後停下來，pos 必須完全停掉 —— 不是降頻，是歸零。"""
    mover = FakeClient("動一下", port=server)
    watcher = FakeClient("看的", port=server)
    await mover.connect()
    await watcher.connect()

    try:
        for i in range(5):
            await mover.send(mover.move_msg(i * 10, 50, 2))
            await asyncio.sleep(0.1)

        await watcher.drain(1.0)  # 吃掉移動期間的封包
        watcher.counts.clear()

        await watcher.drain(2.0)  # 已靜止
        assert watcher.counts["pos"] == 0
    finally:
        await mover.close()
        await watcher.close()
