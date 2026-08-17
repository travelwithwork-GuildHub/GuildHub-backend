# [P28] 與 [P40] review 重點

> 對應任務表 [G2]：Agent 產出 review，[P28] [P40] 為必審項。
> 這兩項之所以必審，不是因為程式難，是因為**寫錯的版本會通過所有直覺的測試**。

---

## 0. 五分鐘版

如果只有五分鐘，做這三件事：

```bash
grep -n "fetch_all\|for r in rows if\|seat_taken" app/api/messages.py app/api/seats.py
```

會有三筆命中，**而且應該只有三筆，全部落在行號 10 以內**：

```
app/api/messages.py:9      rows = await fetch_all_messages()
app/api/messages.py:10     return [r for r in rows if r.sender_id == me]
app/api/seats.py:8         if not await seat_taken(project_id, seat_index):
```

這三行都在檔案開頭的 docstring 裡，是**刻意抄在那裡的反例**——把錯誤寫法
放在正確寫法正上方，日後改這兩個檔案的人一定會先讀到。真正的程式碼從
`messages.py:39` 與 `seats.py:43` 才開始。

若有任何一筆命中落在那之後，就是真的寫錯了。接著：

```bash
sed -n '73,81p' app/api/messages.py    # [P28] WHERE 是否在 SQL 內
sed -n '59,75p' app/api/seats.py       # [P40] 是否 INSERT 後捕捉，而非先查再寫
```

然後看本文件 §3 的三個未決問題 —— 那才是真正需要你裁決的地方。

**注意：這兩份實作都還沒在真的 PostgreSQL 上跑過（[D01] 未完成）。**
下面所有「測試會證明」的敘述，目前都是「測試已寫好，等資料庫」。

---

## 1. [P28] `MessageService.list_for_user`

📄 [app/api/messages.py:63](app/api/messages.py) — 12 行

### 要證明的是什麼

規格書 §4.3：**站內信是全案唯一有實質洩漏風險的資源**，其查詢必須在 SQL 層
帶上主體條件，不依賴應用層過濾。

### 錯的版本

```python
rows = await fetch_all_messages()
return [r for r in rows if r.sender_id == me]
```

這個版本**在一般測試裡結果完全正確**。它有兩個問題：

1. 分頁先套用在「全部」上再過濾 —— 別人的信多到塞滿一頁時，你會看到空清單
2. 任何一次重構只要漏掉那行 list comprehension，全站私訊一次外洩

### 這份實作

```sql
select * from messages
where (sender_id = $1 or recipient_id = $1)
order by created_at desc limit $2 offset $3
```

條件、排序、分頁全部在同一句 SQL 內，Python 端只做 `MessageOut(**dict(r))`。

### 請確認

- [ ] `where` 子句裡有 `sender_id = $1 or recipient_id = $1`，且 `$1` 來自
      `Depends(get_current_user)`，不是來自 query string 或 body
- [ ] `limit` / `offset` 在同一句 SQL 內，不是 Python 切片
- [ ] 回傳前沒有任何 `if`／`filter`／list comprehension 的條件判斷
- [ ] 收件匣同時含寄件與收件是**刻意的**（§3 原則 3：對話隔音，收件匣不隔音）

### 怎麼驗（[D01] 之後）

```bash
.venv/bin/python -m pytest tests/test_messages.py -q
```

其中 `test_message_isolation_survives_paging` 是專門設計來抓錯誤寫法的：先讓
兩個無關的人互寄 30 封，再讓 A 寄一封給 B。正確實作讓 B 看到那一封；「取全部
再過濾」的版本會讓 B 看到**空清單**。

只寄一兩封的測試分不出這兩種寫法 —— 這是它們唯一會給出不同答案的情境。

### 我的信心邊界

- **高**：隔離邏輯本身。條件在 SQL 內，沒有繞過的路徑
- **中**：`select *` 依賴 messages 的欄位與 `MessageOut` 完全一致。這件事有
  `tests/test_schema_consistency.py` 機械化盯著，但它盯的是欄位名，不是型別
- **未涵蓋**：沒有任何測試模擬「同時有大量寄件者」的併發讀取。以本案的量級
  （一天數十到數百筆）不構成風險

---

## 2. [P40] `SeatService.claim`

📄 [app/api/seats.py:43](app/api/seats.py) — 約 20 行

### 要證明的是什麼

規格書 §4.1：**不變式寫在資料庫，因為應用層會有 bug，約束不會。**

### 錯的版本

```python
if not await seat_taken(project_id, seat_index):
    await insert_seat(...)
```

先查再寫之所以錯，不是因為機率低，是因為**它在單機測試裡永遠是對的**。
兩個請求要真的同時到達才會露出來，而那正是發表當天會發生的事。

### 這份實作

沒有 `SELECT`，沒有應用層的鎖。直接 `INSERT`，讓兩個約束說話：

| 約束 | 擋住什麼 | 轉成 |
|---|---|---|
| `primary key (project_id, seat_index)` | 一格兩人 | 409 |
| `unique (project_id, user_id)` | 一人兩格 | 409 |
| `check seat_in_range` | index 不在 0–7 | 400 |
| `foreign key project_id` | 專案不存在 | 404 |

### 請確認

- [ ] `try` 區塊裡**只有一句** `insert`，前面沒有任何 `select`
- [ ] 捕捉的是 `asyncpg.UniqueViolationError`，不是通用 `Exception`
- [ ] 沒有 `SELECT ... FOR UPDATE`、沒有 advisory lock、沒有 `asyncio.Lock`
- [ ] 座位滿即房間滿，沒有另寫容量判斷（§6.3）—— **但請看 §3.1 的問題**

### 怎麼驗（[D01] 之後）

```bash
.venv/bin/python -m pytest tests/test_seats.py -q
```

`test_seat_race_exactly_one_winner` 讓八個各自登入、各自持有 room token 的
client 用 `asyncio.gather` 同時打同一格，斷言恰好一個 201、七個 409，且資料庫
裡只有一列。

另外兩層防假綠：

- `test_seat_race_different_seats_all_succeed` —— 否則「一律回 409」也會通過
- `test_seat_race_at_the_database_level` —— 繞過整個應用層直接以並行連線撞
  資料庫，證明保護來自約束本身而不是來自 FastAPI

### 我的信心邊界

- **高**：一格一人、一人一格。這兩條由約束保證，我的程式碼只負責翻譯錯誤碼
- **低**：**兩種 409 的區分方式**。程式用
  `"user_id" in exc.constraint_name` 判斷是 PK 還是 unique 衝突，而這依賴
  PostgreSQL 自動產生的約束名稱（`seats_pkey` vs
  `seats_project_id_user_id_key`）。若有人日後替約束改名，兩則訊息會悄悄對調
  —— **狀態碼仍然正確，只有文案會錯**。這是我在這份實作裡最不喜歡的一行
- **未涵蓋**：座位數上限（見 §3.1）

---

## 3. 三個我發現但沒有自行決定的問題

守則 §1 規則 7：遇到規格書未定義的情況，停下來提問，不要自行補完。

### 3.1 `seat_count` 上限 ✅ 已依 P1 裁決加上檢查

原本的問題：`seat_index` 只被 `seat_in_range`（0–7）限制，沒有任何地方檢查
`seat_index < projects.seat_count`，所以 `seat_count = 2` 的房間會接受
`seat_index = 7`，規格書 §6.3 的「座位滿即房間滿」不成立。

**P1 裁決：加應用層檢查。** 已實作於
[app/api/seats.py](app/api/seats.py)，但寫法上刻意避開一件事 ——

```sql
insert into seats (project_id, seat_index, user_id, desk_template)
select $1::uuid, $2::smallint, $3::uuid, $4::smallint
from projects
where id = $1::uuid and $2::smallint < seat_count
returning ...
```

**這不是把「先查再寫」請回來了**，關鍵在於查的是哪張表：

| | 查什麼 | 會不會有競爭 |
|---|---|---|
| 危險的先查再寫 | `seats`：這格有沒有人坐？ | 會 —— 兩人同時查都得到「沒有」 |
| 這裡的容量檢查 | `projects.seat_count`：這房間有沒有第 7 格？ | 不會 —— 跟誰在搶無關，成軍後也不再變動 |

兩個不變式正交，所以競爭防護沒有被削弱：同時搶 seat 1 的兩個人一樣會通過
容量檢查、一樣在 PK 上撞、一樣只有一個成功。
`test_capacity_check_did_not_weaken_the_race_protection` 就是釘住這件事。

寫成單一句 `INSERT ... SELECT` 而不是「先 fetchval 再 insert」，是為了成功
路徑少一次來回，也為了不在這個檔案裡留下一段長得像正確寫法的先查再寫。

**請確認**：

- [ ] 容量條件在 `insert` 的 `where` 裡，不是獨立的一次 `select`
- [ ] `row is None` 的診斷查詢只在失敗之後才跑，不在成功路徑上
- [ ] 400（房間沒這格）與 409（這格有人／你已有位子）分得清楚

### 3.2 `read_at` 永遠是 null ✅ P1 裁決：先留著

`messages.read_at` 在規格書 §4 的 schema 裡，但沒有任何程式路徑寫它，任務表
也沒有對應任務。

**P1 裁決：欄位保留，不實作已讀功能。** 記在這裡是為了讓下一個看到這個欄位
的人知道它是刻意空著的，不是漏掉的 —— 不需要再問一次，也不要順手補一個
「標記已讀」端點（守則 §3：即使看起來順手就能加也不要加）。

### 3.3 room token 比對持有人 ✅ 已依 P1 裁決加上

[app/deps.py](app/deps.py) 的 `require_room_token` 原本只驗
`claims.project_id`，現在同時驗 `claims.user_id == 目前登入者`，不符回 403。

測試在 [tests/test_room_token_binding.py](tests/test_room_token_binding.py)，
7 個，**不需要資料庫，現在就是綠的**。

老實說：**今天沒有任何路徑走得到這個分支。** token 只由 `/enter` 簽發並直接
寫進簽發對象自己的 session，所以拿到的一定是自己的。這一行防的是未來 ——
哪天有人加了「從 header 或 body 收 room token」的端點，少了它就會變成可以
借用別人的票。

#### 還沒動的一半：WebSocket 握手 ⚠ 需要你決定

[app/realtime/manager.py](app/realtime/manager.py) 的
`authenticate_handshake` 也驗 room token，但**只驗 project_id，沒驗
user_id**。我沒有一併改，理由是這會改變 REST 之外的行為，而你的指示是針對
我在 §3.3 提的那一處：

| 考量 | 傾向 |
|---|---|
| 一致性：同一種 token 兩個地方驗法不同，日後會有人踩到 | 該加 |
| 安全性：房間是共享密碼制（§6.2 不做成員制），知道密碼的人本來就能簽自己的票 | 加了幾乎不增加防護 |
| 代價：會讓 5 個目前綠的測試變紅（它們用任意字串當 user 簽 token） | 要一併改測試 |
| 副作用：等於強制「進房間一定要先登入」—— 這其實符合 §6.2 | 中性偏好 |

一行程式碼 + 改 5 個測試。要加的話說一聲。

---

## 4. 這兩項的測試涵蓋一覽

| | 已寫 | 已跑過 |
|---|---|---|
| [P28] 隔離、分頁洩漏、收件匣不隔音、自寄被擋 | 8 個 | ❌ 等 [D01] |
| [P40] 八人競爭、不同格全成功、一人兩格、DB 層競爭、門禁 | 9 個 | ❌ 等 [D01] |
| 欄位名一致性（schema ↔ models ↔ SQL） | 19 個 | ✅ 綠 |
| 端點不多不少、未登入全 401 | 24 個 | ✅ 綠 |

**第一次跑會紅一片是正常的** —— 那 52 個測試從沒執行過。但欄位名層級的錯誤
已被靜態檢查掃過，所以紅的應該是邏輯，不是打字。
