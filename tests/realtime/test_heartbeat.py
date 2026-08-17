"""[R25] heartbeat 與逾時偵測。

任務表：「不得依賴 beforeunload」。瀏覽器對「離開」沒有可靠訊號（規格書 §7
談工時統計時也提到同一件事），所以判斷「還在不在」只能靠伺服器主動探測。

機制是 WebSocket 的 ping/pong 控制訊框（RFC 6455）：連線靜止時仍會定期
往返，對端消失就在逾時內被發現。這不違反 §3.2 的「靜止時封包數為 0」——
那說的是位置廣播，ping 是傳輸層的存活探測，兩者不同。
"""

import asyncio
import time

from app.main import presence
from tools.fake_client import FakeClient


async def test_heartbeat_reaps_a_connection_that_vanished(server_fast_ping):
    """模擬斷網：直接中止 TCP，不送 close frame。

    這正是拔網路線／睡眠筆電的樣子 —— 伺服器不會收到任何通知，只能靠
    ping 沒有回應才發現。
    """
    client = FakeClient("斷網的", port=server_fast_ping)
    await client.connect()
    user_id = client.you

    assert presence.get(user_id) is not None, "連上之後應該在 presence 裡"

    client.ws.transport.abort()  # 沒有 close 握手，對端毫無所悉

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if presence.get(user_id) is None:
            return  # 逾時內被清除
        await asyncio.sleep(0.25)

    raise AssertionError("斷網後連線未在逾時內被清除")


async def test_heartbeat_keeps_a_silent_but_live_connection(server_fast_ping):
    """靜止不動的人不可以被誤判為離線。

    §3.2 要求靜止時不送位置封包，所以「很久沒送東西」不能當成離線依據 ——
    只有 ping 沒回應才算。
    """
    client = FakeClient("發呆的", port=server_fast_ping)
    await client.connect()
    user_id = client.you

    try:
        await client.drain(5.0)  # 五秒完全不送任何東西
        assert presence.get(user_id) is not None, "發呆被誤判為離線"
    finally:
        await client.close()
