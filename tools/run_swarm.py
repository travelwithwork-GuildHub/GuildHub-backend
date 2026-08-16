"""[R05] N 併發驅動器與封包計數。規格書 §8：W1 spike 的量測工具。

要回答的問題只有一個：40 條連線同時在，伺服器有沒有垮。

用法：
    python tools/run_swarm.py --n 40 --seconds 60
    python tools/run_swarm.py --n 5 --idle      # 靜止，驗證 pos 封包數為 0

--idle 是規格書 §3.2 的驗收手段：「驗收 A2 時要抓封包確認這件事，不能只看
程式碼」。靜止時 pos 應該一則都不送。
"""

import argparse
import asyncio
import contextlib
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.fake_client import FakeClient  # noqa: E402

CLIENT_HZ = 10  # 附錄 A.2：客戶端最高 10 Hz，且僅在座標變動時送
MAP_W, MAP_H = 1280, 720


async def drive(client: FakeClient, seconds: float, idle: bool, sent: Counter) -> None:
    """一條連線的生命週期：連上、走動、離線。"""
    await client.connect()

    reader = asyncio.create_task(client.drain(seconds))

    if not idle:
        x, y = random.randrange(MAP_W), random.randrange(MAP_H)
        f = random.randrange(4)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(1 / CLIENT_HZ)
            nx = max(0, min(MAP_W, x + random.randrange(-8, 9)))
            ny = max(0, min(MAP_H, y + random.randrange(-8, 9)))
            if (nx, ny) == (x, y):
                continue  # 座標沒變就不送 —— 附錄 A.2
            if nx != x:
                f = 2 if nx > x else 1
            elif ny != y:
                f = 0 if ny > y else 3
            x, y = nx, ny
            with contextlib.suppress(Exception):
                await client.send(client.move_msg(x, y, f))
                sent["move"] += 1

    await reader
    await client.close()


async def main(args: argparse.Namespace) -> int:
    clients = [
        FakeClient(f"bot-{i:03d}", scene=args.scene, host=args.host, port=args.port)
        for i in range(args.n)
    ]
    sent: Counter = Counter()

    started = time.monotonic()
    results = await asyncio.gather(
        *(drive(c, args.seconds, args.idle, sent) for c in clients),
        return_exceptions=True,
    )
    elapsed = time.monotonic() - started

    failures = [r for r in results if isinstance(r, BaseException)]
    received: Counter = Counter()
    for c in clients:
        received.update(c.counts)

    total_recv = sum(received.values())
    print()
    print(f"連線數        {args.n}（失敗 {len(failures)}）")
    print(f"場景          {args.scene}{'（靜止模式）' if args.idle else ''}")
    print(f"實測時間      {elapsed:.1f} s")
    print(f"送出 move     {sent['move']}  ({sent['move'] / elapsed:.1f}/s)")
    print(f"收到總封包    {total_recv}  ({total_recv / elapsed:.1f}/s)")
    for t, n in sorted(received.items(), key=lambda kv: -kv[1]):
        per_client = n / args.n if args.n else 0
        print(f"  {t:<10} {n:>8}  ({n / elapsed:>8.1f}/s，每連線 {per_client:.1f})")

    pos = received["pos"]
    if args.idle:
        verdict = "PASS" if pos == 0 else "FAIL"
        print(f"\n靜止時 pos 封包數 = {pos} → {verdict}（規格書 §3.2 要求 0）")
        return 0 if pos == 0 else 1

    per_client_hz = pos / args.n / elapsed if args.n and elapsed else 0
    verdict = "PASS" if per_client_hz <= CLIENT_HZ + 1 else "FAIL"
    print(f"\n每連線 pos 頻率 = {per_client_hz:.1f} Hz → {verdict}（上限 10 Hz）")

    if failures:
        print(f"\n第一個失敗：{failures[0]!r}")
        return 1
    return 0 if verdict == "PASS" else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GuildHub 併發假客戶端驅動器")
    p.add_argument("--n", type=int, default=40, help="併發連線數")
    p.add_argument("--seconds", type=float, default=10.0, help="持續秒數")
    p.add_argument("--scene", default="lobby")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--idle", action="store_true", help="不移動，驗證靜止時無 pos")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
