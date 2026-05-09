# 表格完整传入 + oversized_tables 字段（2026-05-09）

## 改动背景

用户确认 tokens 充足，去掉表格行数限制，要求完整内容传入 prompt。
同时 DB 记录 oversized 表格供前端展示。

## 改动内容

### 1. `_find_relevant_tables` 返回值类型变更

**旧**：返回 `str`（格式化文本），调用方自己算 oversized
**新**：返回 `dict`：
```python
{
    "text": str,           # 完整表格文本（不限行数）
    "oversized_table_ids": List[str],  # >20行的表格ID列表
    "total_table_count": int,
    "same_chapter_count": int,
    "cross_ref_count": int,
}
```

**影响范围（两处必须同步改）**：
- `scripts/chapter_review/process_chapters_v2.py`（skill 脚本）
- `backend/app/api/reviews.py`（backend API）

### 2. `process_chapter` 调用一次原则

`_find_relevant_tables` 在 `process_chapter` 中**只调一次**，结果（含 oversized_ids）存入 result dict，
传递给 `_review_content`（后者不再重复调）。

避免问题：重复调用导致同一表格被处理两次、或两次结果不一致。

### 3. 匹配策略（同 chapter_num 全部传入）

- **同 chapter_num**：表格**全部**传入，不限数量，完整内容
- **跨章节补充**：关键词匹配最多 10 个，完整内容
- **Oversized 阈值**：>20 行

### 4. DB 字段变更

**表**：`review_inputs`
**新增字段**：`oversized_tables TEXT`（JSON 数组，如 `["t180", "t195"]`）

写入位置（backend API）：
```python
review_inputs.append({
    ...
    "oversized_tables": json.dumps(oversized_table_ids),
})
```

Model：`backend/app/models/models.py` — `ReviewInput.oversized_tables`
Schema：`backend/app/schemas/schemas.py` — `ReviewInputResponse.oversized_tables`

### 5. 前端展示

`frontend/src/views/ReviewInputs.vue`：
- 章节审查表格新增"超大表格"列
- `el-tag type="warning"` 显示数量，hover tooltip 显示具体表格 ID 列表
- 无 oversized 显示 `-`
- `getOversizedTables(row)` 函数解析 JSON 数组

## 推送记录

| 仓库 | 文件 | commit |
|------|------|--------|
| eia-review-agent (backend) | `app/api/reviews.py`, `models.py`, `schemas.py` | `a850a37` |
| eia-review-skill | `scripts/chapter_review/process_chapters_v2.py` | `95e7ae7` |
| eia-review-agent (frontend) | `ReviewInputs.vue` | `393d7f5` |
