# 架构误解纠正：process_chapters_v2.py 和 pre_scan.py 都是独立工具（2026-05-10）

## 背景

2026-05-10 逐条核实 R35 缺陷时，发现 skill 文档中"两处代码必须同步"的描述是**错误的**。

## Production 真实流程（reviews.py）

```
Step 1: extract_chapters_textutil.extract_from_docx()
        → full_text, tables (含 table_number), project_info
Step 2: splitChapters(full_text) → chapters_dict
Step 3: _run_completeness_check() → completeness_findings
Step 4: _run_chapter_rules_async()
        ├── _format_relevant_tables(tables, ch_num)  ← 格式化表格（line 541）
        ├── build_chapter_review_prompt()             ← prompt 结构（line 582）
        └── LLM review
```

关键函数都在 `backend/app/api/reviews.py`：
- `_format_relevant_tables()` line 541 — production 唯一表格格式化
- `_run_chapter_rules_async()` line 875 — production 审查入口

## process_chapters_v2.py 是 Standalone CLI

- `main()` 从 `extract/` 读文件，从不被 `reviews.py` import
- 用途：独立 CLI 调试工具，不参与 production

## pre_scan.py 也是死代码

- `generate_pre_scan_report()` + `verify_table_existence()` 从未被 reviews.py 调用
- CLI 独立使用，从不接入 production

## 2026-05-10 修复的真实影响

| 修改位置 | Production 生效？ |
|---------|-----------------|
| `reviews.py` `_format_relevant_tables()` | ✅ 直接生效 |
| `extract_chapters_textutil.py` `TABLE_NUM_PATTERN` | ✅ `extract_from_docx()` 输出 → tables list → production |
| `process_chapters_v2.py` `_format_single_table()` | ❌ 只影响独立 CLI |
| `pre_scan.py` `verify_table_existence()` | ❌ 死代码 |

## 教训

任何新功能要生效，必须改在 `reviews.py` `_run_chapter_rules_async` 里：
1. `_format_relevant_tables()` line 541 — 改这里影响表格文本
2. `build_chapter_review_prompt()` line 582 — 改这里影响 prompt 结构

不要假设 `process_chapters_v2.py` 或 `pre_scan.py` 的代码会自动进入 production。
