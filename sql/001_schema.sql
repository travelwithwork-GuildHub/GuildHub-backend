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
  avatar_id      smallint not null default 0,
  skills         text[] not null default '{}',
  hours_per_week smallint,
  bio            text check (char_length(bio) <= 300),
  updated_at     timestamptz not null default now()
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
