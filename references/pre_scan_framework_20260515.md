# 预扫描框架：系统性消除LLM机械误判（2026-05-15 完成）

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

### R32测试结果

```
预扫描完成：13章节，179个表格编号，0条数值矛盾，0条引用问题
表2.2-1: ['002']  ← LLM误判B-008-02"表不存在"，预扫描确认存在于ch002
表3.5-31: ['003', '006']  ← 在ch003和ch006复用
```

### 关键API

```python
from pre_scan import generate_pre_scan_report

pre_report = generate_pre_scan_report(extract_dir / "extract")
# 返回：{chapter_count, table_count, table_index, verified_numbers,
#        numeric_contradictions, cross_ref_issues, llm_injection}
```

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
- `review_chapters_async()` 在并发审查开始前调用预扫描，报告保存到 `output/pre_scan_report.json`

### 审查流程（优化后）

```
DOCX → extract → splitChapters
              ↓
        【新增】预扫描（~1秒）
              ↓
        生成 pre_scan_report.json
        生成 llm_injection 文本
              ↓
        注入每个章节prompt → LLM逐章审查
              ↓
        所有章节审查完成 → 入库
```

---

## 阶段3：`post_validate.py`

### 核心API

```python
from post_validate import validate_findings, summarize_flags

validated = validate_findings(raw_findings, pre_scan_report)
summary = summarize_flags(validated)
```

### 校验规则（2026-05-16 更新）

| 规则ID | 场景 | flag |
|--------|------|------|
| B-002-06 / B-008-02 | LLM说"表X不存在"但预扫描索引中有 | `PRE_SCAN_CONTRADICTION` → `_confidence_override=low` |
| B-005-01 / B-005-02 | LLM说数值矛盾但预扫描已验算一致 | `B-005数值矛盾` |
| C-001 | 一致时记为缺陷 | `C-001类：一致时应描述为符合` |
| C-019 | 公众参与跨章节适用 | `C-019跨章节：确认内容是否在概述章节` |
| A类缺陷 | 缺陷描述中无明确法规/标准强制依据关键词 | `A_WITHOUT_EXPLICIT_LAW` → 降级提示 |

### 新增：`cross_validate_findings()`（2026-05-16）

检测LLM缺陷描述与报告原文的矛盾，用于catch高风险规则的阅读理解错误。

```python
from post_validate import cross_validate_findings
validated = cross_validate_findings(raw_findings, full_report_text)
```

**高风险规则**：`C-010-03`, `C-010-01`, `C-010-02`

**机制**：检测缺陷描述中的否定词（缺少/未提及/未引用/未包含），在报告中检索是否存在矛盾的正向表述。若发现矛盾，追加 `_cross_flags` 并设置 `_flag_type=CROSS_VALIDATION_FAILED`。

**适用条件**：仅对高风险规则执行，避免全文检索成本。

---

## 5类LLM误判模式与预扫描消除效果

| 模式 | 例子 | 预扫描能消除？ | 消除方式 |
|------|------|--------------|---------|
| 数据一致误判矛盾 | 813.5=27.8+785.7，LLM判矛盾 | ✅ | 数值预验算 |
| 小结文字误解 | 小结已写明N9超标，LLM判隐瞒 | ❌ | 依赖LLM理解 |
| 表格结构误读 | 多级表头，LLM扫片段就下结论 | ✅ | 表格全量传入 |
| 自创编号 | LLM引用表2.2-1不存在 | ✅ | 表格编号索引 |
| 标准适用性过度解读 | 格式不完整→判定适用错误 | ❌ | 需⚠️判断标准 |

**消除率预估**：表格不存在类+B-005数值矛盾类可基本消除（~50%假阳性）。
