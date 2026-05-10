# 预扫描框架：系统性消除LLM机械误判（2026-05-16 更新）

## 核心思路

LLM 不擅长：机械验算、表编号扫描、跨章节比对。
LLM 擅长：理解规则、判断逻辑、评估合理性。

**原则：机械的事交给代码，LLM只做需要判断的事。**

## 三阶段实现状态

| 阶段 | 文件 | 状态 | Git commit |
|------|------|------|------------|
| 1 | `scripts/chapter_review/pre_scan.py` | ✅ 已完成并测试 | `08ab50d` |
| 2 | `scripts/chapter_review/process_chapters_v2.py` + `scripts/utils/review_by_llm.py` | ✅ 已完成并推送 | `08ab50d` |
| 3 | `scripts/chapter_review/post_validate.py` | ✅ 已完成并推送 | `08ab50d` |

---

## 阶段1：`pre_scan.py`

### 功能

1. **表格编号索引**：扫描报告中所有"表X.X-X"格式编号，记录每个编号出现的章节
2. **数值预验算**：按规则对分项数值做加总验证，±5%容差，过滤不合理大值
3. **章节引用校验**：检测正文引用某编号时该编号是否真实存在

### 表格编号索引的局限与改进（2026-05-16 新增）

**旧问题**：`table_index` 从正文文本扫描"表X.X-X"，但正文里可能用"见下表"代替实际编号——导致某些表被引用但未被索引，且无法区分同名异表（如同一表号在不同章节复用）。

**新方案**（已实现，commit `e59331b`）：
- `extract_chapters_textutil.py` 的 `_extract_single_table()` 从**表格第一行第一格**解析实际表号（如"表3.3-1" → "3.3-1"），写入 `table_number` 字段
- `_tables.json` 中每条记录新增 `table_number` 字段（无则为 null）
- `pre_scan.py` 新增 `verify_table_existence()`：对比"正文引用了的表号"与"实际提取到的 table_number"，发现不匹配时输出 `TABLE_NUMBER_NOT_IN_EXTRACTED` 类型问题，注入 LLM prompt

**注入 LLM 的格式**：
```
**⚠️ 表号真实性警告（预扫描验证过实际表格）：**
- ❌ 第003章：正文引用「表3.3-1」，但实际提取的表格中未找到此表号（上下文：「...引用了表3.3-1...」）
```

**注意**：该校验依赖 `_tables.json` 中的 `table_number` 字段——只有用新版 `extract_chapters_textutil.py`（含 `table_number` 解析）提取的报告才有此字段。对历史报告重新提取即可获得。

### 已验证的数值规则

| type | 分项关键词 | 容差 | max_item过滤 |
|------|-----------|------|-------------|
| 给排水 | A栋+B栋+A2栋 | ±5% | 1000（过滤注册资本类大值） |
| 蒸汽冷凝 | 用水量+清净下水+蒸汽冷凝 | ±5% | 200 |
| 建筑面积 | A栋+B栋+A2栋 | ±5% | 100000 |
| 环保投资 | 设备费+安装费+其他 | ±5% | 100000 |

---

## 阶段2：接入层（已集成到审查流程）

**`scripts/utils/review_by_llm.py`**：
- `review_chapter()` 新增 `pre_scan_injection: str = ""` 参数
- `_build_chapter_review_prompt()` 在规则库之前插入预扫描文本

**`scripts/chapter_review/process_chapters_v2.py`**：
- import `generate_pre_scan_report`
- `review_single_chapter()` / `_review_content()` 新增 `pre_scan_injection` 参数
- `_format_single_table()` 输出格式改为 `表格 5 (ch3) [3.3-1]`，LLM 审查时直接看到实际表号

---

## 阶段3：`post_validate.py`

### 校验规则

| 规则ID | 场景 | flag |
|--------|------|------|
| B-002-06 / B-008-02 | LLM说"表X不存在"但预扫描索引中有 | `PRE_SCAN_CONTRADICTION` → `_confidence_override=low` |
| B-005-01 / B-005-02 | LLM说数值矛盾但预扫描已验算一致 | `B-005数值矛盾` |
| C-001 | 一致时记为缺陷 | `C-001类：一致时应描述为符合` |
| C-019 | 公众参与跨章节适用 | `C-019跨章节：确认内容是否在概述章节` |
| A类缺陷 | 缺陷描述中无明确法规/标准强制依据关键词 | `A_WITHOUT_EXPLICIT_LAW` → 降级提示 |

---

## 5类LLM误判模式与预扫描消除效果

| 模式 | 例子 | 预扫描能消除？ | 消除方式 |
|------|------|--------------|---------|
| 数据一致误判矛盾 | 813.5=27.8+785.7，LLM判矛盾 | ✅ | 数值预验算 |
| 小结文字误解 | 小结已写明N9超标，LLM判隐瞒 | ❌ | 依赖LLM理解 |
| 表格结构误读 | 多级表头，LLM扫片段就下结论 | ✅ | 表格全量传入 |
| 自创编号 | LLM引用表2.2-1不存在 | ✅ | 表格编号索引（文本扫描） |
| 表号真实性 | LLM说"表3.3-1不存在"但表在表格提取数据里 | ✅ | `verify_table_existence()`（用 table_number 字段） |
| 标准适用性过度解读 | 格式不完整→判定适用错误 | ❌ | 需⚠️判断标准 |
