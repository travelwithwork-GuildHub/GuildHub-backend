"""套用 sql/ 底下編號的 .sql 檔。

開工前準備 §1：不用 Alembic 等 migration 框架 —— 一次性實例，schema 已完整
寫在規格書 §4，用編號的 .sql 檔 + 一支套用腳本即可。這就是那支腳本。

用 asyncpg 而不是呼叫 psql：開發機是 Windows，沒有 psql binary；asyncpg 本來
就是相依套件，少一個外部指令就少一個「在我機器上可以」的來源。

用法：
    python tools/apply_sql.py              # 套用 001_schema.sql
    python tools/apply_sql.py --seed       # 連 002_seed.sql 一起
    python tools/apply_sql.py --reset      # 先砍掉 public schema 再重建

--reset 會刪掉所有資料。一次性實例可以這樣做，正式營運的服務不行。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from app import config  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


async def main(args: argparse.Namespace) -> int:
    try:
        conn = await asyncpg.connect(config.DATABASE_URL)
    except Exception as exc:  # noqa: BLE001
        print(f"連不上資料庫：{exc}")
        print(f"DATABASE_URL = {config.DATABASE_URL}")
        return 1

    try:
        if args.reset:
            print("砍掉 public schema 重建（所有資料會消失）")
            await conn.execute("drop schema public cascade; create schema public;")

        files = ["001_schema.sql"]
        if args.seed:
            files.append("002_seed.sql")

        for name in files:
            path = SQL_DIR / name
            if not path.exists():
                print(f"找不到 {path}")
                return 1
            try:
                await conn.execute(path.read_text(encoding="utf-8"))
            except asyncpg.PostgresError as exc:
                print(f"{name} 套用失敗：{exc}")
                return 1
            print(f"已套用 {name}")

        tables = await conn.fetch(
            "select tablename from pg_tables where schemaname = 'public' "
            "order by tablename"
        )
        print("目前的表：", ", ".join(r["tablename"] for r in tables) or "（無）")

        for table in ("profiles", "projects", "seats", "messages"):
            if any(r["tablename"] == table for r in tables):
                n = await conn.fetchval(f"select count(*) from {table}")
                print(f"  {table:<10} {n} 列")
    finally:
        await conn.close()

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="套用 GuildHub 的 schema 與假資料")
    p.add_argument("--seed", action="store_true", help="一併套用 002_seed.sql")
    p.add_argument("--reset", action="store_true", help="先砍掉 public schema")
    raise SystemExit(asyncio.run(main(p.parse_args())))
