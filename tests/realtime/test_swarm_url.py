"""[D05] 前置：假客戶端與 run_swarm 要連得上部署環境的 wss://。

原本只收 host／port，組出來的永遠是 ws://host:port/ws —— 對本機夠用，對
Railway 不行：部署環境只有 HTTPS 的閘道網址，沒有 port 可填，也不能用 ws://
（deploy/README.md §0 第三列）。沒有這個參數，[D05] 的 50 併發複跑只能在
本機做，而本機做的那一次證明不了發表日那台機器的事。
"""

import pytest

from tools.fake_client import FakeClient
from tools.run_swarm import main, parse_args


def test_base_url_replaces_host_and_port():
    c = FakeClient("x", base_url="wss://gw.example.com/ws", host="10.0.0.1", port=1)
    assert c.url == "wss://gw.example.com/ws?scene=lobby"


def test_base_url_still_carries_scene_and_token():
    c = FakeClient(
        "x", scene="room:p1", token="abc", base_url="wss://gw.example.com/ws"
    )
    assert c.url == "wss://gw.example.com/ws?scene=room:p1&token=abc"


@pytest.mark.parametrize(
    "bad",
    [
        "https://gw.example.com/ws",  # 協定錯：websockets 會連，但錯誤訊息跟「後端沒開」長得一樣
        "gw.example.com/ws",  # 沒有協定
        "wss://gw.example.com/ws?scene=lobby",  # scene／token 由參數組，不接受寫死在網址裡
    ],
)
def test_base_url_rejects_what_would_fail_later_and_quietly(bad):
    with pytest.raises(ValueError):
        FakeClient("x", base_url=bad)


async def test_fake_client_connects_through_base_url(server):
    async with FakeClient("走網址的", base_url=f"ws://127.0.0.1:{server}/ws") as c:
        assert c.you  # 收到 hello


async def test_run_swarm_accepts_url(server):
    args = parse_args(
        ["--url", f"ws://127.0.0.1:{server}/ws", "--n", "2", "--seconds", "0.5"]
    )
    assert await main(args) == 0
