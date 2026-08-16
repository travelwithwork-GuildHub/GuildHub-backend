"""PresenceStore。規格書 §3.2 §3.3：位置、狀態文字、在線名單，全部只在記憶體。

守則 §1 規則 5：即時層的任何資料不得寫入資料庫。這個檔案因此完全不 import
app.db，也不該有任何 SQL —— test_presence 有一個測試在盯這件事。

狀態文字「登入時為空白、離線即清空」是結構性保證，不是清理排程：Player 物件
本身就是狀態文字的容器，連線消失時物件一起消失，所以不可能出現過期狀態。
"""

STATUS_MAX_CHARS = 12  # 規格書 §3.3。超過會遮蔽鄰近角色。


class Player:
    __slots__ = ("id", "name", "av", "scene", "x", "y", "f", "st")

    def __init__(self, user_id: str, name: str, scene: str, avatar_id: int = 0):
        self.id = user_id
        self.name = name
        self.av = avatar_id
        self.scene = scene
        self.x = 0
        self.y = 0
        self.f = 0
        self.st = ""  # §3.3：登入時為空白

    def as_dict(self) -> dict:
        """附錄 A.3 的 snapshot 元素。"""
        return {
            "id": self.id,
            "name": self.name,
            "av": self.av,
            "x": self.x,
            "y": self.y,
            "f": self.f,
            "st": self.st,
        }


class PresenceStore:
    def __init__(self):
        self._players: dict[str, Player] = {}
        # scene_id -> 本 tick 內有移動的 user_id。Broadcaster 讀完即清（[R20]）。
        self._moved: dict[str, set[str]] = {}

    def join(self, user_id: str, name: str, scene: str, avatar_id: int = 0) -> Player:
        player = Player(user_id, name, scene, avatar_id)
        self._players[user_id] = player
        return player

    def get(self, user_id: str) -> Player | None:
        return self._players.get(user_id)

    def set_position(self, user_id: str, x: int, y: int, facing: int) -> None:
        """[R12]。座標沒變就不算移動 —— 靜止時封包數必須為 0（§3.2）。"""
        player = self._players.get(user_id)
        if player is None:
            return  # 不替不存在的人建鬼影
        if (player.x, player.y, player.f) == (x, y, facing):
            return
        player.x, player.y, player.f = x, y, facing
        self._moved.setdefault(player.scene, set()).add(user_id)

    def set_status_text(self, user_id: str, text: str) -> None:
        """[R13]。12 字上限由伺服器驗，不依賴前端截斷。

        用字數而非位元組：中文一個字 3 bytes，用 len(bytes) 會讓 4 個字就被擋下。
        """
        if len(text) > STATUS_MAX_CHARS:
            raise ValueError(f"狀態文字上限 {STATUS_MAX_CHARS} 字，收到 {len(text)} 字")
        player = self._players.get(user_id)
        if player is not None:
            player.st = text

    def snapshot(self, scene_id: str) -> list[dict]:
        """[R14]。新進者取得全場快照，之後只收差量。"""
        return [p.as_dict() for p in self._players.values() if p.scene == scene_id]

    def diff_since_last_tick(self, scene_id: str) -> list[tuple[str, int, int, int]]:
        """本 tick 內有移動者，讀完即清。

        回傳空清單代表「沒有人動」。Broadcaster 據此決定整則訊息不送 —— 不是
        送空陣列（[R20]）。
        """
        moved = self._moved.pop(scene_id, None)
        if not moved:
            return []
        out = []
        for user_id in moved:
            player = self._players.get(user_id)
            if player is not None:
                out.append((player.id, player.x, player.y, player.f))
        return out

    def clear(self, user_id: str) -> None:
        """[R27]。離線即清空，狀態文字不得留存至下次登入（§3.3）。"""
        player = self._players.pop(user_id, None)
        if player is not None:
            moved = self._moved.get(player.scene)
            if moved is not None:
                moved.discard(user_id)
