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
    ├── scripts/                      # 核心代码
│   │   ├── chapter_review/           # 章节审查、报告生成
│   │   │   ├── pre_scan.py          # ★ 预扫描脚本（表格索引+数值验算，v2.33+）
│   │   │   ├── post_validate.py      # ★ 后校验层（flag异常LLM输出，v2.33+）
│   │   │   └── process_chapters_v2.py  # 已集成预扫描调用
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

## R31 验证与优化（2026-05-09）

**R31审查结果**：43条缺陷，TRUE 16条(37.2%)、FALSE 13条(30.2%)、UNCERTAIN 14条(32.6%)。排除存疑后真缺陷率55%。

**R31假阳性特征**：
- 第一章新增大量S类假阳性（S-014/S-026/S-028），根因是规则描述与LLM实际检查意图不匹配
- C-004-01/C-005-01 被LLM过度判断为"标准缺失/等级不对"，但报告实际有正确引用
- C-018系列（C-018-02/04/05）：LLM自创数值（22.652 t/a）、混淆排放口类型、混淆"缺失"与"矛盾"

**R32核实结论（2026-05-15）**：同一报告生物制造产业创新中心，R32初审53条 → 核实后43条（删11条假阳性）：
- **严重**：10→4**（删除B-005-02 ch1假阳性；6条降级为较重：B-003-04、B-005-02 ch3、B-008-01、B-012-02、S-019、B-002-04保持严重）
- **较重**：32→35（6条降级）
- **假阳性删除**：完整性ch012、B-002-06（表3.5-31存在）、B-008-02（表2.2-1不存在）、B-005-02 ch002（面积一致）、B-005-02 ch004（N9超标已在小结写明）、B-005-02 ch005（DA009含TSP+PM10正常）、B-005-01 ch002（给排水一致）、B-005-01 ch003（蒸汽冷凝一致）、C-001 ch8（自我矛盾）、C-019-01 ch8（实际无矛盾）
- 详见 `references/r32_defect_verification_20260515.md`

**LLM误判5类新模式（R32核实，区别于R31）**：
1. **数据一致误判矛盾**——数值实际一致（83=27.8+785.7；90=9+30+51），LLM对"矛盾"判断标准过宽
2. **小结文字误解**——章节小结已写明问题，LLM只读小结字面就判"隐瞒"
3. **表格结构误读**——多级表头/合并单元格，LLM扫片段就下结论（表3.5-31实际存在）
4. **自创编号**——缺陷描述引用不存在的表号（表2.2-1不存在），LLM在报告中未找到但仍判缺陷
5. **标准适用性过度解读**——将"格式不完整"升级为"适用错误"

**严重度系统性高估（R33 vs R32，同一报告，2026-05-09，修正）**：
- R32：A类4 / B类35 / C类4（43条）
- R33：A类28 / B类22 / C类3（53条）
- **实质是 A 类暴涨（+24条），非 C 类暴涨**，根因：预扫描信息注入→LLM 保守升级；B 类规则缺降级条件；A 类规则阈值过宽
- 详见 `references/r33_defect_verification_20260509.md` 及 `references/r33_verification_methodology_20260509.md`（向同事分享 Hermes 验证工作法的沟通文档）
同一报告书，R32→R33的审查结果：
- R33：B类22 / C类28 | R32：B类35 / C类4
- C类从4暴涨到28，净转移13条B→C

**根因**：严重度判定逻辑发生了系统性下移，同一内容在R32被判"较重"在R33被判"一般"。

**已核实的严重度低估案例（9条，应升B判C）**：
- C-006-01（冷却塔未列入主要建设内容表）、C-011-03（声环境预测未覆盖所有敏感目标）、C-012d（发酵废气95%收集效率缺乏依据）、C-017-06（应急预案未列明具体联动预案）、C-018-01（备用发电机废气未纳入监测）、C-018-05（DA007排放口分类依据不充分）、C-020-01（芬顿乙腈去除率50%无数据支撑）、C-021-08（有机废气类比条件不匹配，100%乙醇类比混合废气）、C-017-05（事故应急池容积严重不足）

**新假阳性模式（R33核实）**：
| 模式 | 规则 | 根因 |
|------|------|------|
| 标准名称误判 | C-019-03 HJ1305 | LLM不熟悉标准全称《制药工业污染防治可行技术指南 原料药（发酵类、化学合成类、提取类）和制剂类》，自行判断"提取类≠制剂类"错误 |
| 标准适用误判 | C-004-01 | LLM混淆"硫酸"与"硫酸雾"，HJ 2.2-2018附录D确有硫酸浓度限值，报告引用正确 |
| 缺陷描述自相矛盾 | C-010-03 | LLM生成的缺陷描述与原文不符（描述说"缺少NOx"但原文明确写了补充监测含NOx） |
| 规则设计过严 | C-003-01 | 规则要求必须引用国家《分类管理名录》，但引用深圳地方审批名录是合法实践 |

**假阳性率**：R33 C类 5/28 = **18%**

**质量监控方法**：跨版本严重度对比——用同一报告多次审查结果对比严重度分布，可以有效发现严重度漂移问题。下次规则/Prompt更新后，应用同一份报告重新审查验证分布是否恢复。详见 `references/r33_defect_verification_20260509.md`。

**规则优化模式（本轮新增，2026-05-15）**：
- **B-005-01/02 数值容差**：`±5%以内视为四舍五入误差`，同一物质多行分列、排放口同时含TSP+PM10均不构成矛盾
- **B-003-04 无国标因子**：TVOC/NMHC等无国家空气质量标准时，报告采用"类比/参照论证"且逻辑自洽 → 不记缺陷
- **C-010c 三年起算点**：HJ 610-2016 三年有效期**从监测采样时间起算**，不以被引用报告编制时间起算
- **C-001父级规则**：父级C-001无独立审核步骤，子规则C-001-01~04覆盖产业政策各维度；LLM若直接用父级C-001审查"数字问题"易产生自我矛盾缺陷（描述前后打架），此时应删除该缺陷
- **⚠️判断标准字段**：在规则末尾加 `⚠️ XXX判定标准` 字段明确边界，已覆盖S-014/026/028、C-004-01、C-005-01、C-018-02/04/05、B-005-01/02、B-003-04、C-010c
- 问题1：S-014/S-026/S-028/C-004-01/C-005-01 各增加⚠️判断标准字段，明确规则边界
- 问题2：第五章消失确认为LLM非确定性（非代码bug），加章节映射JSON输出+stderr debug日志
- 问题3（验证）：用lxml脚本验证14条存疑项，发现B-005-02的"NMHC=83"、C-020-01"废水限值缺失"、C-018a"雨水监测"均为FALSE（LLM误判）

**规则降级条件字段技术（✅降级条件，2026-05-15 新增）**：
当 LLM 因找不到降级依据而将 B 类升到 A 类时，在规则末尾增加 `✅ 降级条件` 字段，给出明确的"可维持 B 而非升 A"情形。

**适用场景**：规则触发条件合理，但升 A 阈值过宽，导致轻微问题也被判 A。

**技术要点**：
1. 写清"本规则触发须**同时满足**以下 N 个条件"
2. 写出"以下情形可维持 B 而非升 A"的具体条件
3. 写出"仅在 XXX 情况下才升至 A"的充分条件
4. 对标准引用类规则，给出标准全称和适用行业，防止 LLM 记错标准

**已应用降级条件的规则**：
- C-003-01：地方名录合法依据（深圳名录覆盖该行业则不判缺陷）
- C-004-01：HJ 2.2-2018 附录 D 覆盖硫酸雾
- C-019-03：HJ 1305-2023 全称明确（发酵类制药）
- B-005-02：图文不一致但有第三方验证/统计口径差异时维持 B
- B-008-02：产能数据明确但缺规格时维持 B
- S-019：三级触发条件（因子存在 + 标准覆盖 + 完全未引用），缺一不可
- S-027：等级判定错误 + 实质影响双条件

**操作**：修改 `reference/审核规则库.md` 或 `reference/审核规则库-细则补充.md` → `git push` → 重启后端

## R28 prompt优化方案（2026-05-13）→ R29验证结果（2026-05-09）

- **规则文本层**（`审核规则库-细则补充.md`）：
  - S-014~S-018 加注"有图/有文=已判定"
  - S-019 加注 DB44/26/27 区分（DB44/26=废水、DB44/27=废气）+ 甲醇 HJ2.2 参数说明
  - S-022 加注 GB18597/18599 区分（GB18597=危废、GB18599=一般固废）
- **Prompt约束层**（`llm_client.py`）：新增"概念区分提醒"Section；输出约束加跨章节 C-020 去重 + 数据容差 5% 标准
- **生效条件**：改动后需重启后端
- **R29验证结果**：同一报告（R28:147条 → R29:47条，减少68%）；假阳性消除率约75-85%；详见 `references/r29_r28_comparison_20260509.md`
- **详情**：`references/prompt_optimization_r28_20260513.md`

## 假阳性消除：⚠️判断标准 字段技术（2026-05-09）

当 LLM 的判断标准与规则描述不匹配时，会产生系统性的假阳性（如 S-014 查"区划判定行为"但 LLM 实际在查"标准引用格式"）。

**解决pattern**：在规则末尾增加 `**⚠️ 判断标准**` 字段，明确：
1. 本规则**只检查什么**（边界）
2. 本规则**不追究什么**（那是其他规则的职责）
3. 容易混淆的规则对（如 S-014 vs C-004-01）

**已应用判断标准的规则**（2026-05-09 + 2026-05-13）：
- S-014 大气环境功能区划判定：只查"区划判定行为本身"，不追究 GB 3095 版本是否为最新
- S-026 地下水环境评价等级：只查"等级判定结论是否正确"，不查表格格式是否与 HJ 610-2016 表1原文一致
- S-028 土壤环境敏感程度（污染影响型）：只查"根据 HJ 964-2018 的分类规则判定是否准确"，不查"周边有无敏感目标"（那是客观事实）
- C-004-01 标准引用格式：只查"标准号+年代号格式是否正确"，不追究该标准是否最优
- C-005-01 评价等级判定依据：只查"判定依据是否充分"，不查"判定结论是否正确"（那是 C-005-02 的职责）
- **C-018-02** 监测因子/频次/方法：同一物质在不同排放口执行不同标准是**正常现象**，不判为矛盾
- **C-018-04** 污染物排放清单：LLM 必须引用报告原文数值；报告找不到的数值应声明"未找到来源"而非直接报缺陷；累加计算须说明过程
- **C-018-05** 排污口规范化：同一排放口编号不能同时属于两种类型（有组织 vs 无组织），标记为**矛盾**而非"缺失"

**操作步骤**：修改 `reference/审核规则库.md` 或 `reference/审核规则库-细则补充.md` → 重启后端

## 表格提取：两套 lxml 工具的对齐问题（2026-05-09 发现，2026-05-15 记录）

`extract_chapters_textutil.py` 和 `verify_tables.py` **都用 lxml** 解析同一份 DOCX 的 `word/document.xml`，但逻辑细节不同：

| 工具 | 用途 | 表格数 | 有 chapter_num |
|------|------|--------|----------------|
| `extract_chapters_textutil.py` | 审查引擎提取（生产用） | 220 | ✅ 有 |
| `verify_tables.py` | 关键词验证（调试用） | 223 | ❌ 无 |

**差异根因**：`extract_chapters_textutil.py` 的表格遍历逻辑在某些边界情况（嵌套表格空行、单元格合并）与 `verify_tables.py` 不完全一致，导致 3 个嵌套表格被前者跳过。

**表格编号体系两套系统（R32验证，2026-05-15）**：

| 数据源 | 数量 | 标识格式 | 用途 |
|--------|------|----------|------|
| `extract_tables.json` | 220个 | 顺序整数 ID（0,1,2...） | 审查引擎生产数据，含 `chapter_num` |
| `full_text.txt` 文本扫描 | 179个 | 实际表号（如3.5-31、2.2-1） | `pre_scan.py` 验证 LLM 捏造表号 |

- 差值约41个：表格数据存在，但正文里用"见下表"而非"表X.X-X"引用
- **两套系统不在同一维度**：JSON 的 table_id 是提取时的顺序编号，不代表报告里的实际表号
- `pre_scan.py` 的 `table_index` 用文本扫描（179个），目的是抓 LLM 捏造的表号（如"表2.2-1不存在"但文本出现了2次）
- 179 vs 220 的差异对消除 B-008-02/B-002-06 假阳性**影响不大**——LLM 捏造表号时，该表号必须在正文引用才能构成误判，pre_scan 正是通过文本扫描 catch 这一点
- 若需更完整覆盖（220个全量表格索引），需改造 `extract_chapters_textutil.py` 的表格提取流程，将实际表号也存入 JSON

**实践结论**：
- `verify_tables.py` **不能替代**审查引擎的表格提取——没有 `chapter_num`，无法做章节匹配
- 两者应保持逻辑同步，避免调试时验证结果与审查结果对不上
- 同步点：单元格文本提取（`''.join(t.text or '' for t in cell.findall('.//w:t', ns))`）、空行跳过逻辑

**操作**：修改 `extract_chapters_textutil.py` 的表格解析逻辑后，对比 `python3 scripts/utils/verify_tables.py <docx>` 的表格数是否仍为 220，若变为 223 说明对齐了。

---

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

### 全角括号导致 Python SyntaxError（2026-05-15）
- **问题**：修改 `review_by_llm.py` 后 `ModuleNotFoundError: No module named 'pre_scan'`，但 import 路径正确
- **根因**：行97 有中文全角括号 `（` (U+FF08) `）` (U+FF09)，Python tokenize 在该文件中检测到非法字符 `invalid character '（' (U+FF08)`，导致整个模块加载失败（实际是语法错误，但错误被误读为 ModuleNotFoundError）
- **诊断**：`python3 -c "compile(open('review_by_llm.py').read(), 'x', 'exec')"` 直接报 SyntaxError
- **修复**：批量替换文件中所有 `\xef\xbc\x88` → `(` 和 `\xef\xbc\x89` → `)`
- **涉及文件**：`review_by_llm.py`（含中文注释的 prompt 模板）
- **预防**：在 f-string / docstring 中使用中文标点前先做语法检查

### C-017风险物质识别——LLM自创化学名称（2026-05-09 R29验证）
- **问题**：R29审查 C-017-01 描述"风险物质识别遗漏硫酸乙醇"，但报告中表7.2-1列出的是"无水乙醇"和"硫酸"两种独立物质，不存在"硫酸乙醇"这个化合物
- **根因**：LLM将两种独立物质（乙醇+硫酸）组合成一个新的错误化学名称，属于"自创内容"误判
- **修复**：在 C-017 相关规则 prompt 中增加约束——"风险物质应与报告化学品清单一致，不得自行组合/添加；如报告未列出某物质，不得自行添加并判定为遗漏"

### `_find_relevant_tables` 表格匹配策略与两处代码同步（2026-05-09）
- **旧策略**：硬编码 `[:10]` 限制 + 每表限5行 → R29第七章25表只传10个，C-017误判
- **新策略**：
  - 同 `chapter_num` 表格**全部传入**（不限数量），完整行
  - 跨章节关键词匹配最多10个作为补充，完整行
  - Oversized 阈值：>20行（用于记录到DB，不截断）
- **两处必须同步**：`process_chapters_v2.py` 和 `backend/app/api/reviews.py` 各有一份实现，修改时必须同步
- **调用一次原则**：`process_chapter` 调一次 `_find_relevant_tables`，结果传给 `_review_content`，后者不重复调
- **参考**：`references/table_handling_oversized_20260509.md`

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

### `_deduplicate_defects` 去重key错误（2026-05-16）
- **问题**：同 rule_id + chapter 下，用 `title[:30]` 做去重 key，但 LLM 输出的缺陷 `title` 为空字符串（名称在 `description` 里），导致不同描述被误合并为1条
- **修复**：key 改为 `description[:100]`
- **commit**：`dd1e899`（eia-review-agent）

- **严重度统计变量名颠倒（2026-05-16）**
- **问题**：`if sev == "严重": type_b += 1`——变量名和语义颠倒，type_b 存的其实是 A 类
- **影响**：Done 日志标签与实际值不符，但 DB 数据映射正确
- **修复**：统一 `type_a=严重`、`type_b=较重`、`type_c=一般`
- **commit**：`b47cdd8`（eia-review-agent）

### `_deduplicate_defects` 去重key用错字段（2026-05-16）
- **问题**：同 rule_id + chapter 下用 `title[:30]` 做去重 key，但 LLM 输出缺陷时 title 为空（名称在 description 里），导致不同描述被误合并为1条
- **影响**：同一章节同一规则多条不同描述的缺陷只保留1条
- **修复**：key 改为 `description[:100]`
- **commit**：`dd1e899`（eia-review-agent）

### 表格编号解析——真实表号提取（2026-05-16）

**问题**：系统用顺序 `table_id`（1,2,3...）而非报告里的实际表号（如"3.3-1"），LLM 报"表3.3-1不存在"时系统无法对应验证。

**修复**：
1. `extract_chapters_textutil.py` `_extract_single_table()`：从表格第一行第一格解析实际表号（如"表3.3-1" → "3.3-1"），新增 `table_number` 字段
2. `reviews.py` `_format_relevant_tables()` line 567：输出改为 `表格 5 (ch3, [3.3-1])` 格式，LLM 审查时直接看到实际表号
3. `process_chapters_v2.py` `_format_single_table()`：同步更新（但注意：此文件是独立 CLI，production 用的是 reviews.py 里的版本）

**⚠️ 架构澄清**：之前 skill 文档说"两处必须同步"是**误解**。`process_chapters_v2.py` 是独立 CLI，从不被 `reviews.py` import；production 唯一执行的表格格式化在 `reviews.py` line 541 的 `_format_relevant_tables()`。详见 `references/architecture_fixes_20260510.md`。

### 单元测试套件 + Pre-commit hook（2026-05-16）
- **问题**：R31~R35 每轮都 `NameError: name 'project_id' is not defined`，规则优化建议每轮生成 0 条
- **根因**：Step 6 的 try 块里引用了外层不存在的局部变量
- **修复**：改用已查到的 `review.project_id`
- **单元测试位置**：`backend/tests/`（3个新文件，77 tests）
  - `test_validate_findings.py`（C-021降级）
  - `test_severity_mapping.py`（映射）
  - `test_rule_optimization.py`（project_id/去重）
- **Pre-commit hook**：`backend/.git-hooks/pre-commit`（>1MB 禁止 commit，防止 review.db 再入仓库）
- **commits**：`2ee9fc8`（hook+测试）、`dd1e899`（dedup修复）、`e54d18b`（project_id）

### R35 缺陷核实（2026-05-16）
- **方法**：python-docx 提取全文本（段落2574段+表格220个），对37条缺陷逐条搜索关键词验证
- **结果**：37条（严重6/较重27/一般4）；全部37条关键词命中；**命中率100%**（0存疑，0不实）
- **核实脚本**：`docs/verify_r35_defects.py`（workspace docs/，不在git）；`scripts/verify_r35_defects.py`（skill scripts/）
- **核实报告**：`references/r35_defect_verification_20260510.md`（37条逐条核实结论）

## 版本

- v2.39 (2026-05-16) — 表格编号解析（真实表号）+ Pre-commit hook
  - **表格编号解析**：`extract_chapters_textutil.py` 解析实际表号（如"3.3-1"）写入 `table_number` 字段；`reviews.py` `_format_relevant_tables()` 显示实际表号格式
  - **⚠️ 架构澄清**：`process_chapters_v2.py` 是独立 CLI，从不被 `reviews.py` 调用；`pre_scan.py` 的 `verify_table_existence()` 是死代码，production 不执行。详见 `references/architecture_fixes_20260510.md`
  - **Pre-commit hook**：>1MB 文件禁止 commit（防止 review.db 入仓库）
  - **R35核实完成**：37条全属实，命中率100%（首次达到）
  - **commits**：`ef0dbbf`（reviews.py `_format_relevant_tables`）、skill push（extract_chapters_textutil `TABLE_NUM_PATTERN`）
- v2.38 (2026-05-16) — 单元测试+Pre-commit hook+R35缺陷全核实
  - **单元测试**：3个新文件，77 tests（test_validate_findings/severity_mapping/rule_optimization）
  - **Pre-commit hook**：>1MB 文件禁止 commit，防止 review.db 再入仓库
  - **Bug修复**：deduplicate key title→description（`dd1e899`）、severity变量名颠倒（`b47cdd8`）、project_id undefined（`e54d18b`）
  - **R35核实**：37条（严重6/较重27/一般4）；**命中率100%**（37/37属实，0存疑，0不实）
  - 详见 `references/r35_defect_verification_20260510.md`

- v2.37 (2026-05-16) — post_validate 死代码修复
  - **根因**：`validate_findings` 和 `cross_validate_findings` 都在 `if __name__ == "__main__"` 块下，production 从未调用；所有 R34 优化方案（严重度校验/B→A降级）实际未生效
  - **修复**：① pre_scan_report 改为可选参数（None时不阻断校验）② 新增 C-021 系列 B→A 强制降级逻辑 ③ 接入 reviews.py 行409-423 ④ DB写入前统一严重度映射（high→严重/medium→较重/low→一般）
  - **影响**：C-021-01~05 共5条从"严重"降为"较重"，R35起生效
  - 详见 `references/post_validate_dead_code_r34_20260516.md`

- v2.36 (2026-05-16) — R34验证（2026-05-10）：R33→R34严重度分布（严重22→20，较重28→25，一般3→1，总53→46）
  - **P0-1**：A类-2（S-019/S-027消失），但**新BUG：C-021-01~05在R34被误升为严重(A)，规则库定级为较重(B)**，根因post_validate.py只拦截A类降级，未校验B→A升序
  - **P0-2**：S-019/S-027降级条件生效 ✓
  - **P1-1**：C-019跨章节兜底生效，C-019-01~05全系列归零 ✓（待抽查真伪）
  - **P2-1**：`cross_validate_findings`反向效果——C-010从1条→4条；对"缺失/未X"否定词过于敏感，不适用于数据缺失类规则
  - **待办**：修复post_validate.py B→A升序校验；核实S-017；抽查C-019消失真伪
  - 详见 `references/r34_verification_20260510.md`（四轮对比R31→R32→R33→R34）

- v2.36 (2026-05-16) — 全部优化方案10项完成（R33质量评估后续）
  - post_validate：新增A类严重度校验（无明确法规依据时打降级提示）+ `cross_validate_findings()`章节一致性交叉验证（C-010高风险规则）
  - S-019甲醇特殊性：GB37823-2019表1无甲醇专属限值，引用DB44/27-2001不构成明确违规
  - S-027声环境等级：新增HJ2.4-2021第5.2.5条官方答疑，结论正确但论证不充分→维持B
  - C-019跨章节适配：全部5条规则强化"必须检索其他章节"强制表述
  - 4条commit已push：463b215/b7cfdb0/ce5e161/1e68cb3
- v2.35 (2026-05-15) — R33核实：7条规则增加"✅降级条件"字段，消除严重度整体偏高；新增"降级条件字段"技术体系；建立跨版本严重度对比质量监控方法
  - 新增规则优化模式：✅降级条件字段（C-003-01/C-004-01/C-019-03/B-005-02/B-008-02/S-019/S-027共7条）
  - R33严重度核实结论修正：A类暴涨24条（+24），非C类暴涨；严重度整体偏高而非低估
  - 详见 `references/r33_defect_verification_20260509.md` 及 `references/r33_verification_methodology_20260509.md`（向同事分享 Hermes 验证工作法的沟通文档）
- v2.32 (2026-05-15) — R32核实：删11条假阳性（17%→0%）；修复完整性检查附件章节误报（`reviews.py`）；规则优化：B-005-01/02数值容差±5%、B-003-04无国标因子判定、C-010c三年起算点
- v2.29 (2026-05-13) — R31验证：C-018-02/04/05增加⚠️判断标准
- v2.26 (2026-05-09) — 表格数量限制修复（[:10]→不限）；C-017-01误判修复（风险物质识别）；R29 vs R28对比分析（147→47条，-68%）
- v2.22 (2026-05-08) — R26缺陷21根因确认：chunk边界截断，新增`references/r26_defect21_chunk_boundary_20260508.md`
- v2.20 (2026-05-08) — R26缺陷核实方法论，`references/r26_defect_verification_20260508.md`
- v2.16 (2026-05-10) — 字段名统一：164处 `**检查内容**` → `**审核步骤**`；同步到workspace并推送 GitHub commit `e18da4d`
- v2.15 (2026-05-10) — 规则库拆分：B/C主规则审核步骤→179条独立子规则；去重逻辑改进
- v2.13 (2026-05-08) — 步骤编号修正：steps数组从6条减为5条
- v2.12 (2026-05-08) — 删除 global_review，审查流程从7步简化为5步
- v2.10 (2026-05-08) — 删除 G-01~G-05，规则库从57→52条

---

- `references/r35_defect_verification_20260510.md` — R35缺陷逐一核实报告（37条全属实，命中率100%）

**支持文件**：
- `references/skill_directory_structure_20260508.md` — Skill目录结构与运行时文件路径（含路径fallback说明）
- `references/step_index_drift_prevention_20260508.md` — 步骤编号维护规范
- `references/rules_file_structure_20260508.md` — 规则库文件体系说明
- `references/rules_split_20260510.md` — 规则库拆分操作记录
- `references/r25_llm_false_positives_20260510.md` — 7条LLM误判分析
- `references/r28_defect_verification_20260512.md` — R28缺陷核实报告
- `references/r29_r28_comparison_20260509.md` — R29 vs R28对比分析报告（同一报告，68%缺陷减少）
- `references/table_handling_oversized_20260509.md` — 表格完整传入 + oversized_tables 字段（两处代码同步）
- `references/r33_defect_verification_20260509.md` — R33缺陷逐条核实报告（22条A类真缺陷/5条C类假阳性/9条严重度低估）
- `references/r33_verification_methodology_20260509.md` — R33验证方法论沟通文档（向同事分享 Hermes 验证审查结果、给出优化方案的全过程）
- `references/r32_defect_verification_20260515.md` — R32缺陷核实方法论与LLM误判新模式（8条假阳性，含严重程度调整、4类新误判模式）
- `references/completeness_check_bug_20260509.md` — 完整性检查附件章节误报根因（splitChapters vs extract_from_docx 两套逻辑）
- `references/chapter_completeness_bug_20260515.md` — 完整性检查附件章节误报根因（splitChapters vs extract_from_docx 两套逻辑）
- `references/batch_rule_modification_20260515.md` — 批量规则修改操作记录（60条B类规则✅降级条件，从后向前插入避免行号偏移）
- `references/r26_52_defect_verification_20260508.md` — R26 52条逐条核实表
- `references/r26_defect21_chunk_boundary_20260508.md` — 缺陷21根因分析
- `references/prompt_optimization_r28_20260513.md` — R28 prompt优化方案
- `references/quality_evaluation_and_self_evolution.md` — 质量评估与自我进化体系
- `references/post_validate_dead_code_r34_20260516.md` — post_validate 死代码发现与修复（R34根因）
- `references/optimization_plan_completion_r34_20260516.md` — 优化方案10项完成记录及R34验证预期
- `references/r34_verification_20260510.md` — R34四轮对比验证报告（R31→R32→R33→R34，C-021误升BUG/P2-1反向效果/S-017核实）
- `references/ci_debugging_20260508.md` — GitHub Actions CI 典型失败模式
- `references/lxml_table_verification_20260509.md` — lxml直接解析DOCX验证存疑项（223表 vs python-docx的220表）
- `references/审核规则库.md` — 主规则库（B/C/A类，179条）
- `references/审核规则库-细则补充.md` — 细则补充（S类，23条）
- `references/new_fields_automatic_integration_20260510.md` — 新增字段自动生效说明
- `references/pre_scan_framework_20260515.md` — 预扫描三阶段框架（phase1+2+3全部完成并推送）
