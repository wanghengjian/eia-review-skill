# 自检工具 (`self_check.py`) 使用说明

> 创建：2026-05-10
> 用途：对已审查报告的缺陷逐条自动化核实，生成质量报告和优化建议

---

## 用法

```bash
# 完整路径（从任意位置）
~/.hermes/workspace/eia-review/backend/.venv/bin/python \
  ~/.hermes/skills/eia-quick-review/scripts/self_check.py <review_id>

# 例：自检 review_id=35
python self_check.py 35
```

**输出**：
- 终端打印统计摘要（命中率、问题规则、优化建议）
- JSON 报告保存到 `scripts/self_check_report_<review_id>.json`
- 核实结果写入 `defect_verification_results` 表

---

## 核实逻辑

```
缺陷描述文本
  ↓ 正则提取关键词（标准号/章节引用/技术术语/判断词）
关键词列表
  ↓ 全文精确搜索（python-docx 提取报告全文本）
命中统计
  ↓
判定规则：
  关键词命中率 = 命中关键词数 / 总关键词数
  = 1.0 → 属实（✓）
  ≥ 0.5 → 存疑（?）
  < 0.5 → 不实（✗）
```

**关键词提取策略**（已修复）：
- ❌ 旧策略（bug）：直接取整句 description 作为关键词 → 整句不可能匹配 → 命中率 0%
- ✅ 新策略：从 description 提取精准片段
  - 标准号：`DB44`、`GB\d+`、`HJ\d+`
  - 章节引用：`第X章`、`第X表`
  - 技术术语：`声环境功能区`、`地下水`、`废气`、`排放标准`
  - 判断词：`不一致`、`矛盾`、`缺失`、`未说明`、`不合理`

**report_excerpt 优先**：如果缺陷有 LLM 原始摘录（`report_excerpt` 字段），从中提取关键词（比 description 更接近报告原文）。

---

## DB 表结构

```sql
CREATE TABLE defect_verification_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_id INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    rule_id VARCHAR(20),              -- 2026-05-19 新增（旧数据需回填，见下）
    verdict TEXT NOT NULL,           -- 属实/存疑/不实
    hit_rate REAL DEFAULT 0.0,       -- 关键词命中率
    verify_keywords TEXT,            -- 使用的关键词（JSON 列表）
    verify_context TEXT,             -- 命中上下文（JSON）
    created_at TEXT NOT NULL         -- ISO 时间戳
);
```

**回填 rule_id（旧数据，2026-05-19 执行）**：
```sql
UPDATE defect_verification_results 
SET rule_id = (
    SELECT rule_id FROM defects 
    WHERE defects.id = defect_verification_results.defect_id
) 
WHERE rule_id IS NULL;
-- 结果：37条全部回填成功
```

## 后端 API（2026-05-19 重构）

**设计原则**：DB 为唯一数据源，JSON 文件仅作参考，不再作为读取来源。

| 端点 | 路由 | 数据来源 |
|---|---|---|
| GET | `/api/rules/self-check/{review_id}` | DB（join defects 表） |
| POST | `/api/rules/self-check/run/{review_id}` | 调用 self_check.py 脚本写 DB，再查 DB 返回 |

**关键实现细节**：
- SQLAlchemy 2.0：`Row` 对象用 `dict(row._mapping)` 转换，不能用 `dict(row)`（后者报 `TypeError`）
- SQLite 排序：不用 MySQL 的 `FIELD()`，用 `CASE WHEN`：
  ```sql
  ORDER BY CASE severity
      WHEN '严重' THEN 1 WHEN '较重' THEN 2
      WHEN '一般' THEN 3 WHEN '轻微' THEN 4 ELSE 5 END, id
  ```
- 返回格式与前端 `SelfCheck.vue` 的 `buildDefects()` 完全兼容，无需前端改动

---

## 局限性

| 局限 | 说明 |
|------|------|
| 关键词命中率≠绝对准确性 | 部分命中（存疑）不代表真的不实，可能是关键词提取不够精准 |
| 表格内容核实有限 | 只用全文文本搜索，不专门解析表格结构 |
| 语义判断缺失 | "不一致"和"完全缺失"在关键词层面难以区分 |

**建议**：存疑项（?）需要人工复核；不实项（✗）直接触发规则优化建议。

---

## 与 R35 人工核实的对比

| 指标 | 人工核实（R35 早期） | 自检工具（R35 复验） |
|------|---------------------|----------------------|
| 方法 | 逐条读 DOCX 原文 | 关键词搜索 |
| 属实 | 37条 | 20条 |
| 存疑 | 0条 | 17条 |
| 不实 | 0条 | 0条 |
| 命中率（属实/总） | 100% | 54.1% |
| 命中率+存疑 | 100% | 100% |

差异原因：人工核实凭语义判断，自检工具只看关键词是否命中。17条存疑项多为"描述内容在报告里有相关提及但不够精确/完整"的缺陷，人工核实会认属实，工具无法判断语义精确性。

---

## 代码位置

```
~/.hermes/skills/eia-quick-review/scripts/self_check.py
```

**依赖**：
- `python-docx`（从 backend .venv 运行环境）
- DB 路径：`~/.hermes/workspace/eia-review/backend/eia_review.db`
- DOCX 路径：从 DB `projects.file_path` 字段读取
