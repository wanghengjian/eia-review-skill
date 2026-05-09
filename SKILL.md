---
name: eia-quick-review
title: 深圳环评报告书快审技能
description: 对建设项目环境影响评价报告书（DOCX/DOC/PDF）进行快速技术审查，输出结构化审查报告，适用于预审、筛选和上报前核查。
category: productivity
tags: [环评, EIA, 环保, 审查, 深圳]
inputs:
  - name: 报告书文件
    type: file
    required: true
    formats: [docx, doc, pdf]
  - name: 项目名称
    type: string
    required: true
  - name: 会话标识
    type: string
    required: false
outputs:
  - name: 审查报告
    type: file
    path: output/reviews/{项目名}_{会话名}/review_{序号}/审查报告_统一版.md
---

# 深圳环评报告书快审技能

## 功能定位

对建设项目环境影响评价报告书进行**快速技术审查**（2-8分钟），输出结构化审查报告，适用于：
- 收到报告书初步筛选
- 预审 / 上报前核查
- 快速判断能否上报

**与详审的区分**：快审为框架性检查+重点问题识别；详审为逐条规则深度核查（2-4小时）。

## 适用标准

- **HJ 616-2011**《建设项目环境影响技术评估导则》
- **DB4403/T 548-2024**《环境影响评价技术审查规则》（深圳地标）

**代码仓库（2026-05-13）：**
- **项目（后端+前端）**：`https://github.com/WangHengjian/eia-review-agent`（private）
- **技能本体（规则库+核心代码）**：`https://github.com/WangHengjian/eia-review-skill`（public）

> **注意**：`~/.hermes/skills/eia-quick-review/`（技能本体）**不在 Git 追踪范围**。修改规则或代码后需手动同步到 GitHub 备份。

**审查规则体系（179条主规则 + 23条细则补充）**

| 类别 | 名称 | 规则数 | 优先级 |
|------|--------|--------|--------|
| A类 | 编制规范性审查 | 12条 | P1 |
| B类 | 不予批准情形审查 | 58条（15个父规则×各4步拆分） | **P0（底线）** |
| C类 | 技术标准审查 | 109条（22个父规则×各4-6步拆分） | P2 |
| S类 | 细则补充（S-008~S-031） | 23条（原子规则，不拆分） | P2 |

**主规则拆分（2026-05-10）**：B-001~B-015、C-001~C-022、A-001 的每条"审核步骤"拆为独立子规则（如 B-005-01~B-005-04、C-017-01~C-017-06）。原子规则（C-004a/b、C-010a/b/c 等）保持不变。S类不拆分。

**设计原则**：
- `reference/审核规则库.md` — 主规则（B/C/A类，179条）
- `reference/审核规则库-细则补充.md` — 细则补充（S类，23条）
- 每条规则有唯一ID，规则文本通过章节匹配（适用章节字段）直接塞入 LLM prompt
- **规则库是唯一数据源，不存在hardcoded fallback**
- `reference/审核规则库.md.bak_20260508_before_split` — 拆分前备份

## 审查流程（5步）

| 步 | 名称 | 函数 | 说明 |
|----|------|------|------|
| 1 | 内容提取 | `extract_from_docx` / `extract_from_pdf` | 文本+表格（含chapter_num关联）+项目信息 |
| 2 | 章节分割 | `splitChapters` | 按"第X章"标题切分，输出chapters数组 |
| 3 | 完整性审查 | `_run_completeness_check` | 检查缺少标准章节（000~012）或内容过少（<5行） |
| 4 | 逐章审查 | `_run_chapter_rules_async` | 并行最多3章，调用`review_chapter()`注入规则+表格+标准目录 |
| 5 | 汇总报告 | 聚合+去重+入库 | completeness_findings + chapter_defects → 去重 → Defect表 |

后端入口：`backend/app/api/reviews.py`（FastAPI BackgroundTasks → `run_review_task_async`）

## 目录结构（2026-05-13 更新）

**技能目录**：`~/.hermes/skills/eia-quick-review/`（运行时实际读取，**不在 Git**）

```
~/.hermes/skills/eia-quick-review/
├── SKILL.md / WORKFLOW.md      # 技能定义
├── scripts/                    # 核心代码
│   ├── chapter_review/         # 章节审查、报告生成
│   ├── utils/                  # 规则加载器(review_rules_loader.py)、分块器
│   └── generate_rule_keywords.py
└── reference/                  # ★ 运行时规则库（后端读取这里）
    ├── 审核规则库.md            # B/C/A类 179条
    └── 审核规则库-细则补充.md   # S类 23条
```

**GitHub 双仓库架构**：
- `eia-review-agent` — 后端+前端项目（含 CI/DB/前端代码）
- `eia-review-skill` — 技能本体备份（规则库+核心代码）

> ⚠️ **易混淆点**：`main.py` line 88 代码写 `skill_scripts / "reference"`（即 `skills/eia-quick-review/scripts/reference/`），但该目录**不存在**。Python Path 对象在目录不存在时不报错，最终实际读取的是 `skills/eia-quick-review/reference/`。详情见 `references/skill_directory_structure_20260508.md`。

- **更新流程**：修改规则 → ① 同步到 `skill/reference/` ② `git push` 到 `eia-review-skill` 备份 → ③ 重启后端
- **常见错误**：只更新了 `eia-review-skill` GitHub 副本，忘记同步到本地 `skill/reference/`（后端读的是后者）

## R28 prompt优化方案（2026-05-13）→ R29验证结果（2026-05-09）

- **规则文本层**（`审核规则库-细则补充.md`）：
  - S-014~S-018 加注"有图/有文=已判定"
  - S-019 加注 DB44/26/27 区分（DB44/26=废水、DB44/27=废气）+ 甲醇 HJ2.2 参数说明
  - S-022 加注 GB18597/18599 区分（GB18597=危废、GB18599=一般固废）
- **Prompt约束层**（`llm_client.py`）：新增"概念区分提醒"Section；输出约束加跨章节 C-020 去重 + 数据容差 5% 标准
- **生效条件**：改动后需重启后端
- **R29验证结果**：同一报告（R28:147条 → R29:47条，减少68%）；假阳性消除率约75-85%；详见 `references/r29_r28_comparison_20260509.md`
- **详情**：`references/prompt_optimization_r28_20260513.md`

## Bug记录

### 中间产物页面「提取结果」点击详情闪退（DOM过载）(2026-05-13)
- **问题**：R28 extraction记录（220张表格JSON ~500KB）点"查看详情"，Drawer闪一下就消失，无任何报错
- **根因**：`viewExtraction()` 把220张表格的完整JSON（含所有cell数据）全部塞入 `<pre>` 标签渲染，Element Plus的Drawer在构建超大DOM时短暂卡顿 → 视觉上闪退
- **修复**：`viewExtraction()` 改为分块展示——表格只显示前50张的汇总（章节/行列数/行数），全文只显示前3000字预览，不展开原始JSON
- **验证**：推送 commit `4ff5596`；刷新 `/reviews/{id}/inputs` 确认提取结果tab显示正常

### 后端运行旧代码导致中间产物缺失（2026-05-13）
- **问题**：后端PID启动时间早于代码push时间，新审查的extraction/completeness_check记录缺失
- **根因**：后端 `uvicorn` 进程启动后一直用旧代码运行，push新代码后未重启
- **诊断**：`SELECT created_at FROM reviews WHERE id=?` 对比 `git log --oneline -3` 的时间戳；或查DB `review_inputs` 表确认 extraction/completeness_check 类型记录是否存在
- **手动补救**：调用 `extract_from_docx()` + `splitChapters()` 提取，手动 `INSERT` 到 review_inputs 表
- **预防**：代码push后立即检查后端是否需要重启；见 `eia-review-service-management` skill

### 中间产物字段仅对新审查生效 — 历史审查无法回补（2026-05-13）
- **问题**：R28 的 `extraction` / `completeness_check` 记录在 DB 中缺失，前端中间产物页面无数据
- **影响**：新字段只在代码 push **之后重启后端** 才生效；历史审查无法通过回补自动获得新字段数据
- **验证**：`SELECT input_type, COUNT(*) FROM review_inputs WHERE review_id = ? GROUP BY input_type` 正常应有 `chapter(N条) + extraction(1条) + completeness_check(1条)`

### C-017风险物质识别——LLM自创化学名称（2026-05-09 R29验证）
- **问题**：R29审查 C-017-01 描述"风险物质识别遗漏硫酸乙醇"，但报告中表7.2-1列出的是"无水乙醇"和"硫酸"两种独立物质，不存在"硫酸乙醇"这个化合物
- **根因**：LLM将两种独立物质（乙醇+硫酸）组合成一个新的错误化学名称，属于"自创内容"误判
- **修复**：在 C-017 相关规则 prompt 中增加约束——"风险物质应与报告化学品清单一致，不得自行组合/添加；如报告未列出某物质，不得自行添加并判定为遗漏"

### _find_relevant_tables 硬编码10表格限制（2026-05-09 R29验证）🔴 → 已修复
- **问题**：R29 第七章（环境风险评价）有25个相关表格（table_id 180-204），但LLM只看到10个（180-189），表190-204（风险事故情形分析、事故应急池容积计算）全部缺失
- **根因**：`scripts/chapter_review/process_chapters_v2.py` line 227 硬编码 `scored[:10]`
- **影响**：C-017相关缺陷判断因缺少关键表格而出现误判/漏判
- **修复（2026-05-09）**：
  - 同 `chapter_num` 表格**全部传入**（不限数量）
  - 跨章节关键词匹配最多10个作为补充
  - 新增 `_format_single_table()` 格式化函数，单表限5行避免撑爆prompt
  - `process_chapters_v2.py.bak` 已删除
- **验证**：commit `cefd31e`

### Chunk边界截断导致LLM看到标题但无正文（缺陷21根因）(2026-05-08)
- **问题**：缺陷21（B-006-01）被判定为"严重"级假阳性——LLM看到"5.3 大气环境影响预测与评价"标题，判断内容缺失
- **实际根因**：章节005的8000字符chunk切分在5.3标题后截断，5.3的实际内容在下一个chunk，LLM收到的是标题+无正文
- **调试方法**：查DB `review_inputs` 表中该章节的 prompt，看 chunk 的 prompt 在 "5.3 大气" 之后是否直接跳到了"### 相关表格数据"
- **修复**：chunk size 从 8000 改为 **40000 字符**（约2万tokens，留足空间给规则库+表格）
  - 需改3处：`reviews.py` line 698/701 的判断条件和 max_size 参数 + `llm_client.py` line 164 的 `[:8000]` 切片
- **验证结果（R26，2026-05-08）**：所有13个章节均在40000字符以内（最大003工程分析=32750字符），chunk=40000时无需分块，章节005完整为一个chunk

### 同规则重复扣分 (2026-05-10)
- **问题**：C-017在第七章出现16次，B-005跨4章节出现12次；同一逻辑被重复扣分
- **修复**：`_deduplicate_defects()` 改为按 `(rule_id, chapter)` 分组，标题去重，限留3条（severity排序）

### LLM自创"通用"分类 (2026-05-10)
- **问题**：LLM在review_chapter的prompt允许"无则填通用"时，自创了3条"通用-xx"缺陷
- **修复**：prompt改为"无则填'其他'并说明理由"，禁止通用分类

### G类规则实证失效 (2026-05-08)
- **问题**：G-01~G-05 历史0命中，LLM自创"通用-xx"实质对应C-006/C-021/B-005
- **修复**：删除 G-01~G-05

### B-005a/B-005b 拆分回退 (2026-05-08)
- **问题**：拆分后与原始 B-005 检查项高度重叠，同一逻辑被重复计数（9→18条）
- **修复**：回退为单一 B-005

### `check_steps` 字段存储完整规则块（2026-05-10）
- **问题**：前端"审核步骤"列全显示"—"，旧版只存步骤字符串且正则不兼容新 rule_id 格式
- **修复**：改为存完整规则块（含适用章节/参考文件/上级规则/审核步骤），正则支持三种格式：
  - 旧主规则：`B-005`（匹配不到时通过 `**上级规则**` 字段 fallback 到 `B-005-01`）
  - 新子规则：`B-005-01`（精确匹配）
  - 原子规则：`C-004a`、`C-010c`（字母后缀无分隔符，匹配 `C-\\d+[a-zA-Z]`）
- **Markdown→HTML 渲染**：`check_steps` 存 Markdown，后端 `_markdown_to_html()` 转 HTML 后返回，前端 `v-html` 直接渲染
- **列名**："审核步骤" → "审核规则"
- **回填**：R25 不应改动（保持原样）；R26 46/52条已回填 HTML 格式

### 步骤编号与label漂移 (2026-05-08)
- **问题**：删除 `global_review` 后，`steps` 数组仍保留6条，导致label和执行不匹配
- **修复**：数组改为5条，Step 4 label改为"逐章审查"，所有 index 顺移

## 版本
- v2.25 (2026-05-09) — 双仓库架构确认：eia-review-agent(项目) + eia-review-skill(技能本体)；删除 workspace/eia-review/skill 冗余目录；目录结构文档更新
- v2.26 (2026-05-09) — 表格数量限制修复（[:10]→不限）；C-017-01误判修复（风险物质识别）；R29 vs R28对比分析（147→47条，-68%）
- v2.22 (2026-05-08) — R26缺陷21根因确认：chunk边界截断，新增`references/r26_defect21_chunk_boundary_20260508.md`
- v2.20 (2026-05-08) — R26缺陷核实方法论，`references/r26_defect_verification_20260508.md`
- v2.16 (2026-05-10) — 字段名统一：164处 `**检查内容**` → `**审核步骤**`；同步到workspace并推送 GitHub commit `e18da4d`
- v2.15 (2026-05-10) — 规则库拆分：B/C主规则审核步骤→179条独立子规则；去重逻辑改进
- v2.13 (2026-05-08) — 步骤编号修正：steps数组从6条减为5条
- v2.12 (2026-05-08) — 删除 global_review，审查流程从7步简化为5步
- v2.10 (2026-05-08) — 删除 G-01~G-05，规则库从57→52条

---

**支持文件**：
- `references/skill_directory_structure_20260508.md` — Skill目录结构与运行时文件路径（含路径fallback说明）
- `references/step_index_drift_prevention_20260508.md` — 步骤编号维护规范
- `references/rules_file_structure_20260508.md` — 规则库文件体系说明
- `references/rules_split_20260510.md` — 规则库拆分操作记录
- `references/r25_llm_false_positives_20260510.md` — 7条LLM误判分析
- `references/r28_defect_verification_20260512.md` — R28缺陷核实报告
- `references/r29_r28_comparison_20260509.md` — R29 vs R28对比分析报告（同一报告，68%缺陷减少）
- `references/prompt_optimization_r28_20260513.md` — R28 prompt优化方案（6项）
- `references/r26_defect_verification_20260508.md` — R26缺陷核实方法论
- `references/r26_52_defect_verification_20260508.md` — R26 52条逐条核实表
- `references/r26_defect21_chunk_boundary_20260508.md` — 缺陷21根因分析
- `references/prompt_optimization_r28_20260513.md` — R28 prompt优化方案
- `references/quality_evaluation_and_self_evolution.md` — 质量评估与自我进化体系
- `references/ci_debugging_20260508.md` — GitHub Actions CI 典型失败模式
- `references/llm_model_config_20260512.md` — LLM模型配置说明
- `references/审核规则库.md` — 主规则库（B/C/A类，179条）
- `references/审核规则库-细则补充.md` — 细则补充（S类，23条）
- `references/new_fields_automatic_integration_20260510.md` — 新增字段自动生效说明
