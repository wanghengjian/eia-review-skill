# 环评快审 - 规则关键词引擎架构 (2026-04-20)

## 架构设计

从「章节优先」改为「规则优先」：

```
全文提取 → 规则关键词检索 → 上下文提取 → LLM逐条审核 → findings
```

核心文件：
- `scripts/chapter_review/keyword_review_engine.py` — 引擎
- `scripts/generate_rule_keywords.py` — 关键词生成脚本
- `reference/审核规则库_keyword_generated.md` — 含keywords的正式规则库

## 引擎流程

```
load_rules()           解析 Markdown，含 keywords 字段
for each rule:
    keyword_search()   全文找关键词，返回 [(pos, keyword)]
    extract_contexts() 匹配点±800字上下文
    review_rule_with_llm() LLM判断
    → finding dict 或 None
分类(B/C/A) → 生成报告
```

## 规则格式要点

```
### B-001 选址布局规模不符
- **情形**：...
- **keywords**：产业结构调整指导目录、淘汰类、限制类、选址、三线一单...
```

分隔符是 `**keywords**：`（两个 `**` 包裹字段名，之后是 `：`）

## 踩坑记录

### 正则分隔符错误（必须记住）
规则文本是 `**情形**：` — `**` 是粗体，：** 是字段名后的冒号。
错误写：`r'\*\*情形[:：]\*\*'` — 误把 `：**` 当成 `**` 的一部分
正确写：`r'\*\*情形\*\*[:：]'` — 先匹配 `**情形**`，再匹配 `：`

### 文档头分割错误
用 `\n### ` 分割规则块时，第一块是文档头不含 `### 规则ID`。
用前瞻断言解决：`r'\n(?=### [BCA]-\d+)'`

### keywords 字段格式
生成的是 `- **keywords**：...`（字段名只有一对 `**`）
解析器要写 `r'\*\*keywords\*\*[:：]'` 不能写成 `r'\*\*keywords[:：]\*\*'`

## 验证结果（龙岗百旺达废水）

- 全文 111,867 字，108 张表格
- 40 条规则全部含 keywords
- 引擎触发 33 条（重大 11 / 较大 21 / 一般 1）
- B-001/B-002/B-004 未触发是因为文档明确写了合规内容，LLM 正确判断
