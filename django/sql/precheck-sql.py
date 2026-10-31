#!/usr/bin/env python3
"""
precheck-sql.py —— schema 静态预检（CI 第 1 步，先于建库）

目的：在没有任何 PostgreSQL 的情况下，抓出 v6 与 v7.1 都踩过的那类错误——
美元引号不成对、函数体闭合后缺分号。这类错误会让整份 DDL 在第一个
CREATE FUNCTION 处失败并回滚整个建表事务，纯靠"读一遍"发现不了。

检查项：
  1. 开引号写成单个 `$`（如 `AS $` 结尾）—— 必然语法错误
  2. `$$` 出现次数为奇数 —— 引号未配对
  3. 每个闭合 `$$` 之后必须紧跟 `;` —— 否则下一语句被吞并（N1 就是这个错误）

局限：只做引号与终止符层面的静态检查，不能替代真实执行。
      G0 仍必须在真实 PG16 上跑一次。

用法： python3 precheck-sql.py ../schema-v7.sql
退出码：0 通过 / 1 发现缺陷
"""
import re
import sys

STRIP_COMMENT = re.compile(r"--.*$")


def strip_line_comment(line: str) -> str:
    """去掉行注释。不处理引号内的 -- ，对本文件的写法足够。"""
    idx = line.find("--")
    if idx == -1:
        return line
    # 若 -- 出现在单引号字符串内则保守保留（本 schema 无此情况）
    if line[:idx].count("'") % 2 == 1:
        return line
    return line[:idx]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        raw_lines = fh.read().split("\n")

    problems = []

    # 1) 开引号写成单 $
    for no, raw in enumerate(raw_lines, 1):
        code = strip_line_comment(raw).rstrip()
        if re.search(r"\bAS\s+\$\s*$", code):
            problems.append(f"第 {no} 行：函数开引号写成单个 $（应为 AS $$）: {code.strip()}")

    # 收集代码中的 $$ 位置（行号, 列）, 按出现顺序
    positions = []
    for no, raw in enumerate(raw_lines, 1):
        code = strip_line_comment(raw)
        for m in re.finditer(r"\$\$", code):
            positions.append((no, m.start(), code))

    # 2) 奇数个 $$
    if len(positions) % 2 != 0:
        problems.append(
            f"美元引号未配对：共出现 {len(positions)} 次 $$（应为偶数），"
            f"最后一处在第 {positions[-1][0]} 行"
        )

    # 3) 每个"闭合"$$ 之后必须有 ;
    #    出现顺序中，第 0,2,4... 个是开，第 1,3,5... 个是闭
    for i in range(1, len(positions), 2):
        no, col, code = positions[i]
        after = code[col + 2:].lstrip()
        if not after.startswith(";"):
            problems.append(
                f"第 {no} 行：闭合 $$ 之后缺少分号（下一语句会被解析为同一语句的延续）: "
                f"...{code.strip()[-40:]}"
            )

    # 4) 统计信息，便于与 G0 断言对照
    opens = len(positions) // 2
    as_opens = sum(1 for _, _, c in positions[::2] if re.search(r"\bAS\s*\$\$$", c.strip()[: c.find("$$") + 2].strip()))
    print(f"文件: {path}")
    print(f"行数: {len(raw_lines)}")
    print(f"美元引号块: {opens} 个（其中 CREATE FUNCTION 形式 {as_opens} 个）")

    if problems:
        print("\n[FAIL] 发现缺陷：")
        for p in problems:
            print("  -", p)
        return 1

    print("\n[OK] 美元引号成对且均以 ; 终止。仍需执行 G0 真实建库验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
