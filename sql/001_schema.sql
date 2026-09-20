-- GuildHub schema v0.1
-- 規格書 §4 原文，一字不改（任務表 [P04]–[P08]）。
--
-- 這個檔案是全案的唯一真實來源：
--     sql/001_schema.sql  →  app/models.py  →  OpenAPI（自動產生）  →  P2
-- 不得反向手改任何一步（守則 §1 規則 1）。
--
-- 不用 Alembic 或任何 migration 框架 —— 一次性實例，用編號的 .sql 檔。

-- ============ 個人名片（人才牆） ============
create table profiles (
  id             uuid primary key,
  display_name   text not null check (char_length(display_name) between 1 and 20),
  login_id       text unique check (char_length(login_id) between 3 and 32),
  password_hash  text,
  avatar_id      smallint not null default 0,
  skills         text[] not null default '{}',
  hours_per_week smallint,
  bio            text check (char_length(bio) <= 300),
  updated_at     timestamptz not null default now(),
  -- 帳號密碼登入（L3，9/8 裁決）。兩欄都可以是空的 —— 匿名名片就是兩欄皆空，
  -- 發表日的現場進場仍然走那條路（§9）。有一半的名片是登不進去的死帳號，
  -- 所以用 check 綁成全有或全無，而不是靠應用層記得一起寫。
  constraint credentials_all_or_nothing check ((login_id is null) = (password_hash is null))
);

-- ============ 專案，同時就是房間 ============
create type project_status as enum ('recruiting', 'active', 'closed');

create table projects (
  id            uuid primary key default gen_random_uuid(),
  owner_id      uuid not null references profiles(id) on delete cascade,
  title         text not null,
  body          text not null,
  needed_skills text[] not null default '{}',
  status        project_status not null default 'recruiting',
  room_template smallint,
  password_hash text,
  seat_count    smallint not null default 4,
  expires_at    timestamptz not null default now() + interval '7 days',
  updated_at    timestamptz not null default now(),

  -- 狀態為 active 時，房間必須已備妥
  constraint room_ready check (
    status <> 'active' or (room_template is not null and password_hash is not null)
  ),

  -- ---- [BE-G28] 欄位的不變式（2026-09-19，前端 demo 需求清單 1.1） ----
  --
  -- 在此之前 title／body／seat_count 完全沒有約束，POST /api/projects 對空
  -- 標題、0 個座位、9 個座位、300 字標題一律回 201。前端表單擋得住，直接打
  -- API 就繞過了 —— demo 現場有人開 Swagger 試，髒資料就會進看板。
  --
  -- 修法刻意不是在 models.py 加 Field：長度與範圍的唯一真實來源是這個檔案
  -- （守則 §1 規則 1 與 [P15]）。違反時由 app/main.py 的集中 handler 轉成
  -- 400 + code，code 就是下面這些 constraint 的名稱 —— **改名等於改對外
  -- 契約**，前端照這些字串分支。
  --
  -- 數字與前端 src/forms/limits.ts 同源（2026-09-19 對過）。

  -- btrim：空白不只是「短」，只有空白的標題在看板上是一張空卡片。
  --
  -- 第二個參數不能省：btrim(text) 預設**只去半形空格**，tab 與換行不算 ——
  -- 少了它，標題 E'\n\t' 會以 2 個字通過檢查（2026-09-19 被判準抓到）。
  constraint projects_title_length check (
    char_length(btrim(title, E' \t\r\n')) between 1 and 60
  ),
  constraint projects_body_length check (
    char_length(btrim(body, E' \t\r\n')) between 1 and 2000
  ),

  -- 上限 8 是 seats.seat_in_range（0 ≤ seat_index < 8）的另一面，兩個數字
  -- 必須同源：seat_count = 9 的房間第 9 格永遠坐不到（claim_seat 回 400），
  -- seat_count = 0 的房間成軍之後沒有人坐得下。改其中一個就要改另一個。
  constraint projects_seat_count_range check (seat_count between 1 and 8),

  -- 前端要的是「最多 10 項、每項 1–40 字」，但 CHECK 不能含子查詢，unnest
  -- 進不去 —— 逐項檢查只能靠在 schema 裡加一個 immutable 函式，那是這個
  -- repo 沒有的機制。所以這裡擋的是**數量**與**總長**：塞 1000 個 tag 或塞
  -- 一整篇文章都過不了，但單獨一個 300 字的 skill 會過。每項 1–40 由前端
  -- 表單負責，這件事明寫在給前端的回覆文件裡，不是默默放過。
  --
  -- array_to_string 是 STABLE 不是 IMMUTABLE，PostgreSQL 在 CHECK 裡仍然接受
  -- （2026-09-19 對 PostgreSQL 16 實測）。對 text[] 加固定分隔符的結果是
  -- 決定性的，所以不會有「同一列這次過、下次不過」的問題。
  constraint projects_needed_skills_count check (
    coalesce(array_length(needed_skills, 1), 0) <= 10
  ),
  constraint projects_needed_skills_total_length check (
    char_length(array_to_string(needed_skills, ',')) <= 450
  )
);

-- ============ 座位認領 ============
create table seats (
  project_id    uuid not null references projects(id) on delete cascade,
  seat_index    smallint not null,
  user_id       uuid not null references profiles(id) on delete cascade,
  desk_template smallint not null default 0,
  claimed_at    timestamptz not null default now(),

  primary key (project_id, seat_index),      -- 一格只能一人
  unique (project_id, user_id),              -- 一人只能一格
  constraint seat_in_range check (seat_index >= 0 and seat_index < 8)
);

-- ============ 站內信（immutable） ============
create table messages (
  id           uuid primary key default gen_random_uuid(),
  sender_id    uuid not null references profiles(id) on delete cascade,
  recipient_id uuid not null references profiles(id) on delete cascade,
  body         text not null check (char_length(body) between 1 and 2000),
  created_at   timestamptz not null default now(),
  read_at      timestamptz,

  constraint no_self_send check (sender_id <> recipient_id)
);

create index on projects (status, expires_at);
create index on profiles (updated_at desc);
create index on messages (recipient_id, created_at desc);
create index on messages (sender_id, created_at desc);

-- ============ 專案資源（BE-G12，2026-09-16 得到授權加入） ============
--
-- Project Room 裡的外部連結看板（GitHub / Figma / Notion / Drive / Meeting）。
-- 獨立一張表，不在 projects 上加欄位 —— 一個專案的資源是 0..N 筆，塞進
-- projects 就要用陣列或 JSONB，兩者都讓「一筆資源」失去自己的 id 與約束。
--
-- 刻意沒有的欄位：
--   · sort_order —— 排序固定 created_at, id；沒有 reorder 端點
--   · updated_at —— 沒有人會問「這條連結上次改是什麼時候」
--   · deleted_at —— 硬刪除，刪掉就是不見
--   · icon       —— 由 type 推導，存起來就會有兩份真相
--
-- type 用受限 text 加 check，不用 PostgreSQL enum：日後要加一種只改 check，
-- 不必動型別。url 的 regex 只放行 http / https —— 存進去的東西會在別人的
-- 瀏覽器裡被打開，javascript: 與 data: 在資料庫這一層就擋掉。
create table project_resources (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references projects(id) on delete cascade,
  label       text not null check (char_length(label) between 1 and 100
                                   and btrim(label) <> ''),
  type        text not null check (type in ('github', 'figma', 'notion', 'drive', 'meeting')),
  url         text not null check (char_length(url) between 1 and 2048
                                   and url ~* '^https?://[^[:space:]]+$'),
  created_at  timestamptz not null default now()
);

-- 列表固定以 (created_at, id) 排序，且一律限定在單一 project 內。
create index on project_resources (project_id, created_at, id);
