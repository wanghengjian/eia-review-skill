# 表格完整传入 + oversized_tables 字段（2026-05-11 精简版）

## 改动背景

用户确认 tokens 充足，去掉表格行数限制，要求完整内容传入 prompt。
同时 DB 记录 oversized 表格供前端展示。

## 匹配策略（同 chapter_num 全部传入，无跨章节补充）

- **同 chapter_num**：表格**全部**传入，不限数量，完整内容
- **跨章节补充**：已删除（2026-05-11 用户明确要求不需要）
- **Oversized 阈值**：>20 行

## `_find_relevant_tables` 返回值

```python
{
    "text": str,                    # 完整表格文本（不限行数）
    "oversized_table_ids": List[str],  # >20行的表格ID列表
    "total_table_count": int,
}
```

## 两处代码同步（必须同时改）

- `scripts/chapter_review/process_chapters_v2.py`（skill 脚本）
- `backend/app/api/reviews.py`（backend API）

## DB 字段

**表**：`review_inputs`，新增 `oversized_tables TEXT`（JSON 数组）

## 前端展示

`frontend/src/views/ReviewInputs.vue`：章节审查表格新增"超大表格"列，`el-tag type="warning"`。

## 推送记录

| 仓库 | commit |
|------|--------|
| eia-review-agent (backend) | `0cdbcc6` |
| eia-review-skill | `bb7565a` |
| eia-review-agent (frontend) | `393d7f5` |
