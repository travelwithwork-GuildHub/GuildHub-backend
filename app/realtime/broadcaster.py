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
import time

from app.realtime import protocol
from app.realtime.presence import PresenceStore
from app.realtime.scenes import SceneRegistry

log = logging.getLogger("guildhub.realtime")

TICK_SECONDS = 1 / protocol.HZ  # 0.1 s。頻率不得可設定（任務表 [R19]）。

# [BE-G36] WS chat 的兩個上限（2026-09-19，前端清單 2.6）。
# 500 是字數不是 bytes；5 則是每條連線每秒的額度。詳見 _chat_allowed。
CHAT_MAX_CHARS = 500
CHAT_RATE_PER_SECOND = 5


class Broadcaster:
    def __init__(self, scenes: SceneRegistry, presence: PresenceStore):
        self.scenes = scenes
        self.presence = presence
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ [R19] 迴圈

    async def tick_loop(self) -> None:
        """固定 10 Hz。用 sleep 等待，不是 sleep(0) 忙迴圈。

        不能只寫 `await asyncio.sleep(0.1)`：asyncio 判斷計時器到期時會扣掉
        時鐘解析度（Windows 約 15.6 ms），事件迴圈忙碌時 sleep 會提早醒來，
        實測變成 94 ms 一次 ≈ 10.6 Hz。10 Hz 是規格書 §3.2 的硬上限，不是
        目標值，所以這裡以單調時鐘對齊：每個 tick 的時間點事先算好，提早
        醒來就補睡到真正到期。
        """
        next_at = time.monotonic() + TICK_SECONDS
        while True:
            now = time.monotonic()
            while now < next_at:
                await asyncio.sleep(next_at - now)
                now = time.monotonic()

            next_at += TICK_SECONDS
            if next_at <= now:
                # 落後超過一個 tick（GC 停頓之類）就重新對齊，不補送漏掉的
                next_at = now + TICK_SECONDS

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

    async def relay_chat(self, conn, body: str) -> None:
        """[R29] 純廣播，不落地。

        不建 chat_messages 表、不寫任何持久化儲存、不做 scrollback。重整後
        看到空白是正確行為（規格書 §3.4）。

        [BE-G36]：長度與頻率不合的訊息在這裡就被丟掉，不進 broadcast。
        收的是 conn 而不是 (scene_id, user_id, name)，因為節流的單位是**連線**
        —— 同一個人開兩個分頁是兩條連線，各有各的額度。
        """
        if not self._chat_allowed(conn, body):
            return
        await self.broadcast(conn.scene, protocol.chat_out(conn.user_id, conn.name, body))

    def _chat_allowed(self, conn, body: str) -> bool:
        """[BE-G36] 長度上限與每連線每秒的額度。前端清單 2.6。

        在這之前 protocol.py 的 `ChatIn.body: str` 完全沒有限制（REST 的
        messages.body 有 1–2000 的 check，WS 沒有），廣播也沒有節流 ——
        **洗版沒有任何東西擋得住**。前端有送出節流，但那只擋得住守規矩的人。

        ## 三個刻意的決定

        1. **丟棄，不回 err**：附錄 A.2 已經寫著「超頻或不合法的訊息直接丟棄，
           不回錯」，main.py 對超過 12 字的狀態文字就是 continue。照同一個約定。
        2. **不關連線**：在發表現場把人踢下線，他的角色會從世界裡消失，在場
           每個人看到的是「系統壞了」，而不是「那個人被擋了」。
        3. **限制不寫在 protocol.py 的 ChatIn 上**：跟 StatusIn 同一個理由
           —— 12 字上限寫在 PresenceStore.set_status_text，因為那裡才是狀態
           文字真正落腳的地方。chat 不落地，所以落腳處是這裡。

        額度存在 Connection 上而不是 broadcaster 的 dict 裡：連線死掉時額度
        跟著消失，不需要任何清理程式碼，也不會有「斷線了但 key 還在」的洩漏。

        長度算**字數**不是 bytes：中文一個字 3 bytes，用 len(bytes) 會讓 167
        個中文字就被擋下（presence.py 的 STATUS_MAX_CHARS 踩過同一個坑）。
        """
        if not body.strip() or len(body) > CHAT_MAX_CHARS:
            return False

        now = time.monotonic()
        if now - conn.chat_window_start >= 1.0:
            conn.chat_window_start = now
            conn.chat_count = 0

        if conn.chat_count >= CHAT_RATE_PER_SECOND:
            return False

        conn.chat_count += 1
        return True
