# 自检核实体系 — 调试记录（2026-05-19）

## 核实依据不够专家友好

**问题**：原 `summary` 只输出 `"甲苯(3处); 乙醇(2处)"` — 专家看不出判断理由。

**修复**（`self_check.py` `verify_defect()` 函数）：

```python
# 属实：属实。命中：甲苯(3处)；乙醇(2处)。规则要求内容在报告对应章节有明确描述，可作为有效缺陷。
# 存疑：存疑。部分命中：废水(5处)；未找到：噪声、固体废物。证据不足以明确判定，建议人工复核。
# 不实：不实。未在指定章节找到规则要求的关键词（4个关键词均无命中），可能为LLM编造或规则指向章节有误，建议核实原文。
```

## `verify_context` 字段太大导致 HTTP 响应截断

**症状**：curl 直接测 API 返回 204 条，浏览器只收到 37 条。

**根因**：`defect_verification_results.verify_context` 每条记录几百 KB（JSON 化的 LLM 原文片段），204 条总计 40-50MB，HTTP 响应被截断。

**修复**：GET 查询去掉 `v.verify_context` 列，`verify_result.hits` 改为空数组 `[]`。

## 自检并发无锁 — 重复点击覆盖问题

**症状**：用户快速点两次"重新自检"，旧记录被部分覆盖，数据库有 204 条（51 × 4 次）。

**修复**：加 `fcntl.flock` 文件锁 + `DELETE FROM defect_verification_results WHERE review_id = ?` 插入前清理。

## `filterDefects` 未定义警告

**症状**：`Property "filterDefects" was accessed during render but is not defined`。

**根因**：`el-select` 上写了 `@change="filterDefects"`，但该函数不存在 — `filteredDefects` 是 computed，自身响应 `defectFilters.verdict` 变化，不需要额外 handler。

**修复**：删掉 `@change="filterDefects"`，保留 `v-model="defectFilters.verdict"` 即可。

## 自检记录累积 — 每次应覆盖而非追加

**问题**：`self_check.py` 只有 INSERT，没有先 DELETE。

**修复**：插入前加一行 `DELETE FROM defect_verification_results WHERE review_id = ?`。
