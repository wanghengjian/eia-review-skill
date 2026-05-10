# post_validate 死代码发现与修复（R34 根因，2026-05-16）

## 问题现象

R34 审查（2026-05-10）运行后，优化方案全部未生效：
- C-021 系列 5 条从"较重"升为"严重"，但规则库明确定级为 B→较重
- A 类严重度校验逻辑从未触发
- `cross_validate_findings()` 章节交叉验证结果与预期不符

## 根因

`scripts/chapter_review/post_validate.py` 中两个核心函数：

```python
def validate_findings(raw_findings, pre_scan_report: Dict) -> List[Dict]:
    ...

def cross_validate_findings(all_defects: List[Dict]) -> List[Dict]:
    ...

if __name__ == "__main__":
    # 测试代码：从未被 production 调用
    test_results = validate_findings(...)
    cross_validate_findings(test_results)
```

两个函数都在 `if __name__ == "__main__"` 块下，属于**测试代码**。Production 调用链（`reviews.py` → `run_review_task_async`）从未 import 或调用它们。

## 诊断方法

1. **追踪调用链**：`reviews.py` → `run_review_task_async()` → `_deduplicate_defects()` → DB写入，全程无 `import post_validate`
2. **语法验证**：`python3 -c "from chapter_review import post_validate; print('OK')"` — 模块能加载不代表函数被调用
3. **日志验证**：新 review 运行时无 `B→A demoted` 日志 → 函数未被执行
4. **代码搜索**：`grep -r "validate_findings\|cross_validate_findings" backend/app/` — 无结果即未接入

## 修复方案

### 1. make pre_scan_report 可选

**原代码**（会提前 return 跳过所有校验）：
```python
def validate_findings(raw_findings, pre_scan_report: Dict):
    if not pre_scan_report:
        return raw_findings  # ← 阻断所有后续校验
```

**修复后**：
```python
def validate_findings(raw_findings, pre_scan_report: Dict = None):
    if pre_scan_report is None:
        pre_scan_report = {}  # ← 继续执行，只跳过预扫描校验
```

### 2. B→A 强制降级逻辑（C-021 系列）

在严重度校验块之后新增：

```python
for defect in validated:
    severity = defect.get("severity", "medium")
    rule_id = defect.get("rule_id", "")
    desc = defect.get("description", "")
    
    # C-021 系列：无法规依据时强制降为 medium
    if (severity == "high" 
        and rule_id.startswith("C-021")
        and not any(kw in desc for kw in [
            "不符合《", "违反", "必须执行", "排放限值", "浓度限值",
            "超标", "超总量", "无组织排放", "排污许可证",
            "总量替代", "未批先建", "豁免范围", "违法",
            "禁止", "淘汰", "不符合准入"
        ])):
        defect["severity"] = "medium"
        defect["_severity_flag"] = "B_TO_A_DEMOTED"
```

### 3. 接入 reviews.py

在 `_deduplicate_defects` 之后、DB写入之前：

```python
# 行 408-423（_deduplicate_defects 之后）
_ensure_skill_path()
from chapter_review.post_validate import validate_findings
validated = validate_findings(all_defects)
demoted = [d for d in validated if d.get("_severity_flag") == "B_TO_A_DEMOTED"]
logger.info(f"[Review {review_id}] B→A demoted: {len(demoted)} defects")
all_defects = validated
```

### 4. 严重度统一映射

`validate_findings` 输出英文 `high/medium/low`，DB 写入前统一映射为中文：

```python
severity_map = {"high": "严重", "medium": "较重", "low": "一般"}
for defect in all_defects:
    defect["severity"] = severity_map.get(defect.get("severity", ""), defect.get("severity", ""))
```

## 关键教训

| 检查项 | 操作 |
|--------|------|
| 新增函数后检查调用链 | 用 `grep -r "function_name" backend/app/` 确认有调用点 |
| `if __name__ == "__main__"` 块下的代码 | **不信任**——属于测试代码，production 不会运行 |
| 优化方案不生效 | 先查函数是否被 import，再查调用链是否通 |
| 严重度映射不一致 | LLM 输出英文、后端期望中文、中文DB——三个环节必须统一 |

## 相关文件

- `scripts/chapter_review/post_validate.py` — 核心修改：pre_scan可选 + B→A强制降级（commit f9e092a）
- `backend/app/api/reviews.py` — 接入点：行409-423（commit 4c4c1f1）

## 影响范围

R34（commit 前后对比）：
- C-021-01~05：5条从"严重"降为"较重"
- 严重度分布：严重 20→15（-5），较重 25→30（+5）
- 后续 R35 生效
