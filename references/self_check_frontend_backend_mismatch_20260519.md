# SelfCheck.vue 前后端链路不通问题（2026-05-19）

## 问题现象

前端 `SelfCheck.vue` 页面点"重新自检"后，显示的数据与预期不符（显示空或旧数据）。

## 根因：两套存储路径

| 组件 | 路径 | 说明 |
|------|------|------|
| 后端 API `POST /rules/self-check/run/{id}` | 调用 `scripts/self_check.py` → 写 `defect_verification_results` 表 | ✅ DB |
| 后端 API `GET /rules/self-check/{id}` | 读 `~/.hermes/skills/eia-quick-review/scripts/self_check_report_{id}.json` | ❌ JSON 文件 |
| 前端 `SelfCheck.vue` `loadResult()` | 调用 `GET /rules/self-check/{id}` | 读 JSON |

**结果**：自检运行后数据写进 DB，但前端从 JSON 文件读，读不到。

## 现状数据

- `defect_verification_results` 表：37 条记录（R35 的 37 条缺陷已核实）
- `self_check_report_35.json` 文件：存在（但可能过期）

## 修复方案

### 第一步：新建 API 从 DB 聚合

在 `reviews.py` 新增：
```
GET /api/reviews/{review_id}/verification
```
从 `defect_verification_results` 表聚合数据，返回结构：
```json
{
  "review_id": 35,
  "total": 37,
  "hit": 20,
  "doubt": 17,
  "miss": 0,
  "hit_rate": 0.541,
  "defects": [
    {
      "defect_id": 1,
      "rule_id": "B-005",
      "verdict": "属实",
      "hit_rate": 1.0,
      "keywords_used": ["DB44/27", "乙醇"],
      "summary": "DB44/27(4处); 乙醇(2处)"
    }
  ]
}
```

### 第二步：改造 SelfCheck.vue

`loadResult()` 改为调用 `GET /api/reviews/{id}/verification`，不再调用 `GET /rules/self-check/{id}`。

### 第三步：人工核实（待实施）

新建表 `review_manual_checks`，新增 `POST /api/reviews/{review_id}/manual-check`，ReviewResult.vue 加判定按钮。

## 相关文件

- 后端：`backend/app/api/reviews.py`（需新增 endpoint）
- 前端：`frontend/src/views/SelfCheck.vue`（需改造 loadResult）
- 自检脚本：`~/.hermes/skills/eia-quick-review/scripts/self_check.py`
- DB 表：`defect_verification_results`
