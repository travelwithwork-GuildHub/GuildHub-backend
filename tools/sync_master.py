"""把 後端任務表-Agent.csv 的進度同步到 任務表-全案.csv 的後端列。

兩份表都是人要看的：Agent 表是後端每天在動的工作表，全案表是三軌（後端／
前端-互動／前端-應用）合起來的總覽。同一件事寫在兩個地方，落後是必然的 ——
所以同步這件事不能靠記得，要能重跑。

**只同步 Status / 現況 / 進度 / 實際完成日 四欄。** Task、Owner、Document 一律
不動，因為全案表刻意把跨軌參照改寫成全案代碼（[G2] 的「BE-P28 BE-P40 為必審
項」、[G3] 的「救援 FA-09」），覆蓋回去會把這些指向弄壞。Owner 只比對並回報
不一致，不修改。

配對靠代碼：Agent 表的 Task 以 `[R01]` 開頭，全案表的代碼欄是 `BE-R01`。
配不起來就是錯誤（回傳 1），不會安靜跳過 —— 安靜跳過的話新增的任務會永遠
停在「未開始」，而那正是這支腳本要解決的問題。

用法：
    python tools/sync_master.py            # 同步，並在有變動時留下 .bak
    python tools/sync_master.py --check    # 只檢查，不寫檔；有落差回傳 1

--check 適合在更新 Agent 表之後拿來確認自己沒忘記，或掛進 CI。
"""

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

# 兩份 CSV 在 repo 外面一層（規格書、守則、任務表都放在專案資料夾根目錄）。
DOCS_DIR = Path(__file__).resolve().parent.parent.parent
MASTER = DOCS_DIR / "任務表-全案.csv"
AGENT = DOCS_DIR / "後端任務表-Agent.csv"

SYNC_COLUMNS = ["Status", "現況", "進度", "實際完成日"]
BACKEND = "後端"
CODE_PREFIX = "BE-"


def read_csv(path: Path) -> list[list[str]]:
    """讀成原始列。newline='' 讓 csv 模組自己處理行尾，欄位裡的換行才不會被吃掉。"""
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def write_csv(path: Path, rows: list[list[str]]) -> None:
    """寫回 UTF-8 無 BOM、CRLF —— 與原檔一致，Excel 開起來不會變樣。"""
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\r\n").writerows(rows)


def index_by_code(agent: list[list[str]]) -> tuple[dict[str, list[str]], int]:
    """Agent 表 → {BE-R01: 該列}。回傳 (對照表, 沒有代碼標記的列數)。"""
    header = agent[0]
    task_col = header.index("Task")
    table: dict[str, list[str]] = {}
    broken = 0

    for row in agent[1:]:
        if not any(row):
            continue
        hit = re.match(r"\[([^\]]+)\]", row[task_col])
        if hit is None:
            print(f"  Agent 表這列的 Task 沒有 [代碼] 開頭：{row[task_col][:40]}")
            broken += 1
            continue
        code = CODE_PREFIX + hit.group(1)
        if code in table:
            print(f"  Agent 表有兩列都是 {code}")
            broken += 1
        table[code] = row

    return table, broken


def main(args: argparse.Namespace) -> int:
    for path in (MASTER, AGENT):
        if not path.exists():
            print(f"找不到 {path}")
            return 1

    master, agent = read_csv(MASTER), read_csv(AGENT)
    m_header, a_header = master[0], agent[0]

    missing = [c for c in SYNC_COLUMNS if c not in m_header or c not in a_header]
    if missing:
        print("兩份表的欄位對不上，缺：", "、".join(missing))
        return 1

    m_col = {c: m_header.index(c) for c in SYNC_COLUMNS}
    a_col = {c: a_header.index(c) for c in SYNC_COLUMNS}
    m_owner, a_owner = m_header.index("Owner"), a_header.index("Owner")

    by_code, broken = index_by_code(agent)
    if broken:
        print(f"Agent 表有 {broken} 列無法取得代碼，先修那幾列再跑")
        return 1

    changed: list[tuple[str, str, str]] = []
    unmatched: list[str] = []
    owner_diff: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for row in master[1:]:
        if not any(row) or row[1] != BACKEND:
            continue

        code = row[0]
        source = by_code.get(code)
        if source is None:
            unmatched.append(code)
            continue
        seen.add(code)

        before = [row[m_col[c]] for c in SYNC_COLUMNS]
        after = [source[a_col[c]] for c in SYNC_COLUMNS]
        if before != after:
            changed.append((code, before[0], after[0]))
            for c, value in zip(SYNC_COLUMNS, after):
                row[m_col[c]] = value

        if row[m_owner] != source[a_owner]:
            owner_diff.append((code, row[m_owner], source[a_owner]))

    # 反向檢查：Agent 表新增的任務若沒有補進全案表，總覽就會少一列而看不出來。
    orphans = sorted(set(by_code) - seen)

    if unmatched:
        print("全案表這些代碼在 Agent 表找不到：", "、".join(unmatched))
    if orphans:
        print("Agent 表這些任務不在全案表裡（要手動補一列）：", "、".join(orphans))

    if args.check:
        if changed:
            print(f"有 {len(changed)} 列落後：")
            for code, before, after in changed:
                print(f"  {code}  {before or '（空）'} → {after}")
        else:
            print("全案表的後端狀態與 Agent 表一致")
    elif changed:
        shutil.copyfile(MASTER, MASTER.with_suffix(MASTER.suffix + ".bak"))
        write_csv(MASTER, master)
        print(f"更新 {len(changed)} 列，原檔備份為 {MASTER.name}.bak")
        for code, before, after in changed:
            print(f"  {code}  {before or '（空）'} → {after}")
    else:
        print("已是最新，沒有寫檔")

    if owner_diff:
        print("Owner 不一致（本腳本不動 Owner，請人決定哪邊對）：")
        for code, in_master, in_agent in owner_diff:
            print(f"  {code}  全案={in_master} / Agent={in_agent}")

    if unmatched or orphans:
        return 1
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="同步任務表-全案.csv 的後端狀態")
    p.add_argument("--check", action="store_true", help="只檢查不寫檔，有落差回傳 1")
    raise SystemExit(main(p.parse_args()))
