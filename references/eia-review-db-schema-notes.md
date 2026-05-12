# EIA Review DB Schema Notes

## Table Schemas

### `review_analysis_snapshots`
| Column | Type | Note |
|--------|------|------|
| id | INTEGER | NOT auto-increment — must be explicitly listed in INSERT |
| review_id | INTEGER | NOT NULL |
| project_id | INTEGER | NOT NULL |
| snapshot_key | TEXT | NOT NULL |
| metric_name | TEXT | NOT NULL |
| metric_value | REAL | NOT NULL |
| detail | TEXT | nullable |
| created_at | TEXT | default CURRENT_TIMESTAMP |

**⚠️ INSERT pitfall**: Always list all 7 columns explicitly:
```sql
INSERT INTO review_analysis_snapshots (id, review_id, project_id, snapshot_key, metric_name, metric_value, detail, created_at) VALUES (?,?,?,?,?,?,?,?)
```
Using `INSERT INTO ... VALUES (...)` without column list (relying on SQLite's implicit column order) will fail with `Incorrect number of bindings supplied` because the 7 placeholders won't match the 8 columns (including `id`) unless `id` is also supplied or the column list is declared.

### `rule_stats`
| Column | Type | Note |
|--------|------|------|
| id | INTEGER | NOT auto-increment |
| rule_id | TEXT | NOT NULL |
| snapshot_type | TEXT | NOT NULL |
| snapshot_value | REAL | NOT NULL |
| review_count | INTEGER | default 0 |
| detail | TEXT | nullable |
| computed_at | TEXT | default CURRENT_TIMESTAMP |

**⚠️ Same pitfall**: All 6 columns must be listed explicitly.

## Chapter Normalization Reference

EIA reports contain chapters in many alias forms. Use `normalize_ch()` consistently:

```python
CHAPTER_ALIASES = {
    '001': ['001','第一章','第一章  总则','第一章总则','1总则','总则','概述总则'],
    '002': ['002','第二章','第二章  项目概况','第二章项目概况','2项目概况','项目概况'],
    '003': ['003','第三章','第三章  项目工程分析','3项目概况','工程分析'],
    '004': ['004','第四章','第四章  环境现状调查与评价','4现状调查','现状调查'],
    '005': ['005','第五章','第五章  建设项目环境影响评价','第五章项目施工期和运营期环境影响评价','5影响评价','影响评价'],
    '006': ['006','第六章','第六章  环境保护措施及其可行性论证','第六章项目环境保护与污染防治措施','6环保措施','环保措施'],
    '007': ['007','第七章','第七章  环境风险评价','7风险评价','风险评价'],
    '008': ['008','第八章','第八章  生态影响评价','8生态影响','生态影响'],
    '009': ['009','第九章','第九章  环境管理与监测计划','9环境管理','环境管理'],
    '010': ['010','第十章','第十章  建设项目合理性分析','10合理分析','合理分析'],
    '011': ['011','第十一章','第十一章  环境影响评价结论','11结论','结论'],
    '012': ['012','第十二章','第十二章  附件与附录','12附件附录','附件附录'],
}

def normalize_ch(ch_raw):
    if not ch_raw or str(ch_raw).strip() in ['全局','全局审查','概述']:
        return '全局'
    ch = str(ch_raw).strip()
    for norm, aliases in CHAPTER_ALIASES.items():
        if ch in aliases:
            return norm
    return ch  # fallback: return as-is
```

## Rule ID Classification

```python
valid_pattern = re.compile(r'^([ABC])-(\d+)(?:-\d+)?$|^S-(\d+)$')
general_pattern = re.compile(r'通用|GENERAL|GEN', re.IGNORECASE)

def is_formal_rule(rid):
    return bool(valid_pattern.match(str(rid)))

def is_general_rule(rid):
    return bool(general_pattern.search(str(rid)))
```

- **Formal rules**: A-001, B-005, C-017-01, S-014, etc.
- **General rules**: "通用-xx", "GENERAL", "GEN", etc. — should decline over time
- **HJ-standard rules**: HJ 2.1-2016, HJ 610-2016, etc. — non-formal, counts as "other"

## Quality Metrics Formulas

| Metric | Formula |
|--------|---------|
| 正规规则命中率 | `正规规则缺陷数 / 总缺陷数` |
| 通用类比例 | `通用类缺陷数 / 总缺陷数` |
| 章节漂移率（单审） | `漂移缺陷数 / 正规规则缺陷数`（漂移 = 缺陷章节不在规则适用章节内，且非全局） |
| 章节漂移率（规则级） | 同上，按规则聚合，筛选缺陷数≥3的规则 |
| 项目重复率 | Jaccard = `|A ∩ B| / |A ∪ B|`（规则ID+章节去重），多审项目两两计算取均值 |

## Relevant DB Paths

- DB path: `/Users/power/.hermes/workspace/eia-review/backend/eia_review.db`
- Backend code: `/Users/power/.hermes/workspace/eia-review/backend/app/api/reviews.py`
