"""Broadcaster：10 Hz tick 迴圈。規格書 §3.2。

守則 §4.3 點名這裡最容易被善意寫錯：

    # 錯 —— 每幀送，訊息量變六倍並開始丟包
    async for frame in render_frames():
        await broadcast(positions)

節流不是最佳化，是硬性要求。三件事必須同時成立：
  1. 頻率固定 10 Hz，且不可設定（可設定就會有人在測試環境調高）
  2. 只送本 tick 內有移動者
  3. 無人移動時「整則訊息不送」，不是送空陣列
"""

import asyncio
import logging

from app.realtime import protocol
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry

log = logging.getLogger("guildhub.realtime")

TICK_SECONDS = 1 / protocol.HZ  # 0.1 s。頻率不得可設定（任務表 [R19]）。


class Broadcaster:
    def __init__(self, scenes: SceneRegistry, presence: PresenceStore):
        self.scenes = scenes
        self.presence = presence
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ [R19] 迴圈

    async def tick_loop(self) -> None:
        """固定 10 Hz。用 sleep(0.1) 而不是 sleep(0) 忙迴圈。"""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                # 一個 scene 出事不該讓整個迴圈死掉，否則全場停止更新
                log.exception("tick 失敗")

    async def tick(self) -> None:
        for scene_id in self.scenes.ids():
            moved = self.diff_positions(scene_id)
            if not moved:
                continue  # 整則訊息不送 —— 靜止時封包數為 0
            await self.broadcast(scene_id, protocol.positions(moved))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.tick_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------ [R20] 差量

    def diff_positions(self, scene_id: str) -> list[tuple[str, int, int, int]]:
        """僅本 tick 內有移動者。空清單代表整則訊息不送。"""
        return self.presence.diff_since_last_tick(scene_id)

    # ------------------------------------------------------------ [R21] 廣播

    async def broadcast(self, scene_id: str, payload: str) -> None:
        """送給該 scene 的全部成員，不跨 scene。

        沒有可見度判斷 —— 隔音由連線歸屬達成（規格書 §3.1）。payload 是已序列化
        的字串：同一則訊息送給數十條連線，序列化一次就好。

        送給自己也一起送。規格書 §2.1 的 15,600 則／秒是「託管服務按接收端計費」
        的算法；自架是每個 tick 每條連線收一則合併訊息（40 × 10 = 400 則／秒），
        為此逐條過濾掉自己反而要為每個接收者各序列化一次，得不償失。
        """
        for conn in self.scenes.members(scene_id):
            try:
                await conn.ws.send_text(payload)
            except Exception:  # noqa: BLE001
                # 送不出去代表連線已死，交給 [R24] disconnect 清理
                log.debug("送出失敗，略過 %r", conn)

    async def relay_chat(self, scene_id: str, user_id: str, name: str, body: str):
        """[R29] 純廣播，不落地。

        不建 chat_messages 表、不寫任何持久化儲存、不做 scrollback。重整後
        看到空白是正確行為（規格書 §3.4）。
        """
        await self.broadcast(scene_id, protocol.chat_out(user_id, name, body))
