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
