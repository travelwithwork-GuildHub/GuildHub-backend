"""SceneRegistry。規格書 §3.1：每個連線屬於且僅屬於一個 scene。

隔音不是靠可見度判斷達成的，是靠連線歸屬 —— 廣播只走 scene 的成員名單，
所以大廳與房間天然互不可見。不要在這裡加任何過濾邏輯（[R21]）。

全部在記憶體。這一層的任何資料都不寫資料庫（守則 §1 規則 5）。
"""

import re

# scene_id 只有兩種格式（[R07]）：大廳，或某個專案的房間。
_SCENE_ID = re.compile(r"^(lobby|room:[0-9a-zA-Z\-]+)$")


class Scene:
    def __init__(self, scene_id: str):
        self.id = scene_id
        self.members: set = set()

    def __repr__(self):
        return f"Scene({self.id}, {len(self.members)} 人)"


class SceneRegistry:
    def __init__(self):
        self._scenes: dict[str, Scene] = {}

    def get_or_create(self, scene_id: str) -> Scene:
        if not _SCENE_ID.match(scene_id or ""):
            raise ValueError(f"不合法的 scene_id：{scene_id!r}（只接受 lobby 或 room:{{id}}）")
        scene = self._scenes.get(scene_id)
        if scene is None:
            scene = self._scenes[scene_id] = Scene(scene_id)
        return scene

    def add_member(self, scene_id: str, conn) -> None:
        """一條連線只屬於一個 scene（[R08]）。切換場景 = 關掉重開。"""
        self.get_or_create(scene_id).members.add(conn)

    def remove_member(self, scene_id: str, conn) -> None:
        """移除不存在的成員不視為錯誤 —— 斷線與心跳逾時可能重複觸發。"""
        scene = self._scenes.get(scene_id)
        if scene is not None:
            scene.members.discard(conn)

    def members(self, scene_id: str) -> list:
        """在線名單（[R09]）。不存在的 scene 回空清單，不建立它。

        回傳快照（list）而非活的 set —— 廣播期間可能有人進出，直接迭代
        set 會 RuntimeError。
        """
        scene = self._scenes.get(scene_id)
        return list(scene.members) if scene is not None else []

    def ids(self) -> list[str]:
        """現存的 scene id。Broadcaster 每個 tick 迭代它（[R19]）。"""
        return list(self._scenes)

    def online_count(self, scene_id: str) -> int:
        """該 scene 當前連線數（[R10]）。

        規格書 §5.2：走廊的門要顯示「專案名稱 + 當前在線人數」。專案名稱在
        PostgreSQL、人數在這裡，兩者由 api/rooms.py 合併 —— 那是兩層唯一的
        接觸點（附錄 B ★）。
        """
        scene = self._scenes.get(scene_id)
        return len(scene.members) if scene is not None else 0
