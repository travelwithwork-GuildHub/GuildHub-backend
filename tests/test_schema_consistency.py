"""schema → models → SQL 三者的一致性檢查。不需要資料庫。

開工前準備 §3.1 規定的方向是：

    sql/001_schema.sql  →  app/models.py  →  OpenAPI  →  P2

這個檔案是那條紀律的自動化版本。它不驗行為，只驗「名字對不對得上」——
而那正是目前最可能出錯的地方：軌 P 的實作與測試都還沒在真的 PostgreSQL 上
跑過，一個打錯的欄位名要等到 [D01] 之後才會炸，而且會混在一堆真正的失敗
裡看不出來。

打錯字現在就能抓，不必等資料庫。
"""

import re
from pathlib import Path

import pytest

from app import models

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "001_schema.sql"

SPEC_TABLES = {"profiles", "projects", "seats", "messages"}

# 這些關鍵字開頭的行是約束，不是欄位
_NOT_A_COLUMN = re.compile(
    r"^\s*(primary\s+key|unique|constraint|check|foreign\s+key)\b", re.I
)


def parse_schema() -> dict[str, set[str]]:
    """從 001_schema.sql 抓出每張表的欄位名。"""
    text = re.sub(r"--[^\n]*", "", SCHEMA.read_text(encoding="utf-8"))
    tables: dict[str, set[str]] = {}

    for match in re.finditer(
        r"create\s+table\s+(\w+)\s*\((.*?)\n\);", text, re.S | re.I
    ):
        name, body = match.group(1), match.group(2)
        columns = set()
        depth = 0
        line_buffer = ""
        # 以頂層逗號斷句，避免被 check(...) 裡的逗號騙到
        for char in body + ",":
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if char == "," and depth == 0:
                stripped = line_buffer.strip()
                if stripped and not _NOT_A_COLUMN.match(stripped):
                    columns.add(stripped.split()[0])
                line_buffer = ""
            else:
                line_buffer += char
        tables[name] = columns
    return tables


SCHEMA_TABLES = parse_schema()


# --------------------------------------------------------------------- schema


def test_schema_defines_exactly_the_four_tables():
    """守則 §1 規則 3：禁止建立規格書 §4 之外的資料表。"""
    assert set(SCHEMA_TABLES) == SPEC_TABLES


@pytest.mark.parametrize("table", sorted(SPEC_TABLES))
def test_every_table_has_columns(table):
    assert SCHEMA_TABLES[table], f"{table} 沒解析到任何欄位，解析器可能壞了"


def test_schema_is_not_defined_by_an_orm():
    """守則 §2：ORM 作為查詢工具可以，作為 schema 定義來源不行。"""
    for path in ROOT.joinpath("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for banned in ("sqlalchemy", "declarative_base", "alembic"):
            assert banned not in text.lower(), f"{path.name} 出現了 {banned}"


# --------------------------------------------------------- models ↔ schema
#
# 這幾個 model 是直接用 dict(row) 展開建構的，欄位對不上就會在執行期炸掉。

EXACT = [
    (models.ProfileOut, "profiles"),
    (models.MessageOut, "messages"),
]

SUBSET = [
    (models.ProjectOut, "projects"),
    (models.SeatOut, "seats"),
    (models.ProfileUpdate, "profiles"),
    (models.SeatClaim, "seats"),
    (models.ProjectCreate, "projects"),
]


@pytest.mark.parametrize("model,table", EXACT, ids=[m.__name__ for m, _ in EXACT])
def test_response_model_matches_its_table_exactly(model, table):
    """這兩個走 `returning *`，所以欄位必須完全相等 —— 少一個會缺欄位，
    多一個會是 Pydantic 的必填欄位拿不到值。"""
    assert set(model.model_fields) == SCHEMA_TABLES[table]


@pytest.mark.parametrize("model,table", SUBSET, ids=[m.__name__ for m, _ in SUBSET])
def test_model_fields_all_exist_in_the_table(model, table):
    unknown = set(model.model_fields) - SCHEMA_TABLES[table]
    assert not unknown, f"{model.__name__} 有 {table} 沒有的欄位：{sorted(unknown)}"


def test_project_out_never_carries_the_password_hash():
    """§6.2 共享密碼。它存在資料庫裡，但不該有任何路徑把它送出去。"""
    assert "password_hash" in SCHEMA_TABLES["projects"]
    assert "password_hash" not in models.ProjectOut.model_fields


def test_project_columns_constant_matches_the_response_model():
    """projects 的查詢用手寫欄位清單（避免 select * 把 password_hash 撈出來），
    那份清單必須跟 ProjectOut 對得上。"""
    from app.api.projects import _COLUMNS

    listed = {c.strip() for c in _COLUMNS.split(",")}
    assert listed == set(models.ProjectOut.model_fields)


# ------------------------------------------------------------- SQL ↔ schema


def sql_literals() -> list[tuple[str, str]]:
    """撈出 app/ 底下所有看起來像 SQL 的字串常數。"""
    found = []
    for path in ROOT.joinpath("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'"((?:[^"\\]|\\.)*)"', text):
            body = match.group(1)
            if re.search(r"\b(select|insert into|update|delete from)\b", body, re.I):
                found.append((path.name, body))
    return found


def test_sql_only_touches_tables_that_exist():
    """抓錯字：select ... from profile（少個 s）現在就會紅，不必等 [D01]。"""
    offenders = []
    for filename, sql in sql_literals():
        for table in re.findall(
            r"\b(?:from|insert\s+into|update|delete\s+from)\s+([a-z_]+)", sql, re.I
        ):
            if table in ("pg_constraint", "pg_tables", "pg_get_constraintdef"):
                continue
            if table not in SCHEMA_TABLES:
                offenders.append(f"{filename}: 表 {table!r} 不存在 — {sql[:60]}")
    assert not offenders, "\n".join(offenders)


def test_sql_insert_column_lists_are_real_columns():
    offenders = []
    for filename, sql in sql_literals():
        for table, columns in re.findall(
            r"insert\s+into\s+([a-z_]+)\s*\(([^)]*)\)", sql, re.I
        ):
            for column in (c.strip() for c in columns.split(",")):
                if column and column not in SCHEMA_TABLES.get(table, set()):
                    offenders.append(f"{filename}: {table}.{column} 不存在")
    assert not offenders, "\n".join(offenders)


def test_sql_update_set_targets_are_real_columns():
    offenders = []
    for filename, sql in sql_literals():
        for table, assignments in re.findall(
            r"update\s+([a-z_]+)\s+set\s+(.*?)(?:\s+where\b|$)", sql, re.I | re.S
        ):
            for column in re.findall(r"([a-z_]+)\s*=", assignments):
                if column not in SCHEMA_TABLES.get(table, set()):
                    offenders.append(f"{filename}: {table}.{column} 不存在")
    assert not offenders, "\n".join(offenders)


def test_seed_only_inserts_into_real_tables():
    seed = (ROOT / "sql" / "002_seed.sql").read_text(encoding="utf-8")
    for table, columns in re.findall(
        r"insert\s+into\s+(\w+)\s*\(([^)]*)\)", seed, re.I
    ):
        assert table in SCHEMA_TABLES, f"假資料寫進不存在的表 {table}"
        for column in (c.strip() for c in columns.split(",")):
            assert column in SCHEMA_TABLES[table], f"{table}.{column} 不存在"
