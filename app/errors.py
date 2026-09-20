"""帶機器可讀 `code` 的 HTTP 錯誤，以及資料庫 check 的訊息對照表。

與 `app/passwords.py`、`app/room_token.py` 同一層級的小模組，不是新的抽象層
（守則 §1 規則 6）—— 它沒有包住任何東西，只是讓 code 有一個宣告的地方。

## 為什麼需要這個

前端 2026-09-19 清單 1.4：座位的兩種 409（「你已經在這個房間有座位了」與
「這個座位已經有人了」）要做不同的處理，但現在只能比對中文字串，文案一改
前端就壞。他們把那兩句話釘進了契約測試裡。

修法刻意**不是**把 `detail` 換成 `{"code": ..., "message": ...}`：那會讓前端
現有的比對當場失效，而發表前兩邊各只剩一個部署窗口。改成在回應最外層多一個
欄位，`detail` 一個字不動：

    {"detail": "這個座位已經有人了", "code": "seat_taken"}

前端要改讀 `code` 的時候再改，不必跟後端同一天上線。

## code 是對外契約

`ApiError` 的 code 與下面 constraint 名稱都是前端拿來分支的字串，等同端點
名稱 —— **改名要通知前端**，跟改 `tests/test_contract.py` 的 EXPECTED 一樣。

422 是唯一沒有 code 的狀態碼：那是 FastAPI 對請求本身的驗證（型別錯、欄位
缺），格式由 FastAPI 決定，我們不碰。給它加 code 會讓前端要處理兩種 422。
"""

from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTPException 加一個 code。

    用子類別而不是在每個 raise 帶 headers 再由 handler 搬進 body：後者能動，
    但「錯誤碼藏在 header 裡」這件事下一個讀這段程式的人要查很久。

    code 寫在 raise 的地方，不是在 main.py 放一張 (狀態碼, 文案) → code 的
    對照表 —— 那張表會依賴中文字串，正是這次要消滅的東西。
    """

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


# --------------------------------------------------- 資料庫 check 的中文訊息
#
# key 是 constraint 名稱，同時也是回給前端的 code。沒有列在這裡的 constraint
# 仍然會是 400，code 仍然是 constraint 名稱，只是訊息換成通用句 —— 所以
# **日後新增的 check 不會再漏成 500**，最壞的情況只是訊息不夠具體。
#
# 匿名 check 的名稱由 PostgreSQL 自動產生，格式是 {table}_{column}_check。
CHECK_VIOLATION_MESSAGES = {
    # projects（[BE-G28]，具名）
    "projects_title_length": "標題長度必須在 1–60 字之間",
    "projects_body_length": "內文長度必須在 1–2000 字之間",
    "projects_seat_count_range": "座位數必須在 1–8 之間",
    "projects_needed_skills_count": "需要的技能最多 10 項",
    "projects_needed_skills_total_length": "需要的技能加起來太長了",
    # profiles（匿名，名稱由 PostgreSQL 產生）
    "profiles_display_name_check": "顯示名稱長度必須在 1–20 字之間",
    "profiles_bio_check": "自我介紹最多 300 字",
    "profiles_login_id_check": "帳號長度必須在 3–32 字之間",
    "credentials_all_or_nothing": "帳號與密碼必須一起給",
    # messages
    "messages_body_check": "訊息長度必須在 1–2000 字之間",
    "no_self_send": "不能寄信給自己",
    # seats / projects 的既有約束
    "seat_in_range": "座位編號超出範圍",
    "room_ready": "房間未備妥，無法成軍",
}

GENERIC_CHECK_VIOLATION = "資料不符合限制"
