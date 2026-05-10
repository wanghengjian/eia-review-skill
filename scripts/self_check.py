#!/usr/bin/env python3
"""
自检脚本：对指定审查的缺陷进行自动化核实

用法:
  python3 self_check.py <review_id>
  python3 self_check.py 35

输出:
  - 每条缺陷的核实结果（属实/存疑/不实）
  - 命中率统计
  - 规则优化建议（JSON + 摘要）

DB:
  ~/.hermes/workspace/eia-review/backend/eia_review.db
"""

import sys
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime

# ── python-docx ──────────────────────────────────────────────────────────────
try:
    from docx import Document
except ImportError:
    import site
    venv = next((p for p in site.getsitepackages()
                 if '.venv' in p and 'python3' in p), None)
    if venv:
        sys.path.insert(0, venv)
    from docx import Document

# ── 路径配置 ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
# scripts → eia-quick-review → skills → ~/.hermes → ~/.hermes/workspace/eia-review/backend
BACKEND_DIR = SCRIPT_DIR.parent.parent.parent / "workspace" / "eia-review" / "backend"
DB_PATH = BACKEND_DIR / "eia_review.db"

# ── 严重度映射 ───────────────────────────────────────────────────────────────
SEVERITY_MAP = {"严重": "A", "较重": "B", "一般": "C", "轻微": "C"}
SEVERITY_ORDER = ["严重", "较重", "一般", "轻微"]

# ── 核实结果常量 ─────────────────────────────────────────────────────────────
REAL = "属实"       # 关键词命中，有明确证据
UNREAL = "不实"   # 关键词不命中或LLM编造
UNCERTAIN = "存疑" # 部分命中但证据不足


# ═══════════════════════════════════════════════════════════════════════════
# 核心功能
# ═══════════════════════════════════════════════════════════════════════════

def load_docx(docx_path: str):
    """
    提取DOCX全文本（段落+表格）
    返回: (paragraphs: list[str], tables: list[str], full_text: str)
    """
    doc = Document(docx_path)

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    tables = []
    for tbl in doc.tables:
        rows = []
        for row in tbl.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            tables.append("\n".join(rows))

    full = "\n".join(paragraphs) + "\n---\n表\n---\n".join(tables)
    return paragraphs, tables, full


def search_in_text(keyword: str, full: str, context_chars: int = 120) -> list[dict]:
    """
    在全文中精确搜索关键词，返回命中片段列表
    """
    if not keyword or not full:
        return []

    hits = []
    for m in re.finditer(re.escape(str(keyword)), str(full)):
        start = max(0, m.start() - context_chars)
        end = min(len(full), m.end() + context_chars)
        snippet = full[start:end].replace("\n", " ").strip()
        hits.append({
            "keyword": keyword,
            "position": m.start(),
            "context": snippet,
        })
    return hits


def verify_defect(keywords: list[str], full: str, chapter: str, description: str) -> dict:
    """
    核实单条缺陷

    策略:
    - 关键词全部命中 → 属实
    - 关键词部分命中 → 存疑（需人工确认）
    - 关键词零命中 → 不实（LLM编造或指向错误章节）

    返回:
    {
        "verdict": "属实|存疑|不实",
        "hit_rate": 0.0-1.0,
        "hits": {kw: [hit, ...]},
        "summary": str
    }
    """
    if not keywords:
        return {
            "verdict": UNCERTAIN,
            "hit_rate": 0.0,
            "hits": {},
            "summary": "无验证关键词",
        }

    all_hits = {}
    total_hits = 0
    for kw in keywords:
        hits = search_in_text(kw, full)
        all_hits[kw] = hits
        total_hits += len(hits)

    hit_rate = sum(1 for kw, h in all_hits.items() if h) / len(keywords)

    if hit_rate == 1.0:
        verdict = REAL
    elif hit_rate >= 0.5:
        verdict = UNCERTAIN
    else:
        verdict = UNREAL

    hit_detail = "; ".join(
        f"{kw}({len(h)}处)" for kw, h in all_hits.items() if h
    ) or "无命中"

    return {
        "verdict": verdict,
        "hit_rate": round(hit_rate, 2),
        "hits": {kw: [{"context": h["context"]} for h in hits]  # hits是dict列表
                 for kw, hits in all_hits.items() if hits},
        "summary": hit_detail,
    }


def analyze_results(results: list[dict]) -> dict:
    """
    汇总核实结果，计算质量指标
    """
    total = len(results)
    real = sum(1 for r in results if r["verdict"] == REAL)
    uncertain = sum(1 for r in results if r["verdict"] == UNCERTAIN)
    unreal = sum(1 for r in results if r["verdict"] == UNREAL)

    hit_rate = real / total if total > 0 else 0
    precision = real / (real + unreal) if (real + unreal) > 0 else 0  # 精度（排除存疑）
    drift_rate = unreal / total if total > 0 else 0  # 漂移率

    # 按规则分组
    by_rule = {}
    for r in results:
        rid = r["rule_id"]
        if rid not in by_rule:
            by_rule[rid] = {"total": 0, "real": 0, "unreal": 0, "uncertain": 0, "items": []}
        by_rule[rid]["total"] += 1
        by_rule[rid]["items"].append(r)
        if r["verdict"] == REAL:
            by_rule[rid]["real"] += 1
        elif r["verdict"] == UNREAL:
            by_rule[rid]["unreal"] += 1
        else:
            by_rule[rid]["uncertain"] += 1

    # 按严重度分组
    by_severity = {s: {"total": 0, "real": 0, "unreal": 0} for s in SEVERITY_ORDER}
    for r in results:
        sev = r.get("severity", "一般")
        if sev in by_severity:
            by_severity[sev]["total"] += 1
            if r["verdict"] == REAL:
                by_severity[sev]["real"] += 1
            elif r["verdict"] == UNREAL:
                by_severity[sev]["unreal"] += 1

    # 命中率低于阈值的问题规则
    problem_rules = []
    for rid, stats in by_rule.items():
        if stats["total"] >= 2:  # 至少2条才统计
            r_hit_rate = stats["real"] / stats["total"]
            if r_hit_rate < 0.8:
                problem_rules.append({
                    "rule_id": rid,
                    "total": stats["total"],
                    "real": stats["real"],
                    "hit_rate": round(r_hit_rate, 2),
                    "verdict": "低命中率" if stats["unreal"] > 0 else "高存疑率",
                })

    return {
        "total": total,
        "real": real,
        "uncertain": uncertain,
        "unreal": unreal,
        "hit_rate": round(hit_rate, 3),
        "precision": round(precision, 3),
        "drift_rate": round(drift_rate, 3),
        "by_severity": by_severity,
        "by_rule": {
            rid: {"total": s["total"], "real": s["real"], "unreal": s["unreal"],
                  "uncertain": s["uncertain"]}
            for rid, s in by_rule.items()
        },
        "problem_rules": sorted(problem_rules, key=lambda x: x["hit_rate"]),
    }


def generate_optimization_suggestions(results: list[dict], analysis: dict) -> list[dict]:
    """
    根据核实结果生成优化建议
    """
    suggestions = []

    # 1. 规则优化建议（命中率低的规则）
    for pr in analysis["problem_rules"]:
        rid = pr["rule_id"]
        items = [r for r in results if r["rule_id"] == rid]

        # 找不实或存疑的样本
        unreal_items = [r for r in items if r["verdict"] == UNREAL]
        uncertain_items = [r for r in items if r["verdict"] == UNCERTAIN]

        if unreal_items:
            # 典型的误判案例
            sample = unreal_items[0]
            suggestion = {
                "type": "rule",
                "priority": "high" if pr["hit_rate"] < 0.5 else "medium",
                "rule_id": rid,
                "summary": f"规则{rid}命中率{pr['hit_rate']}%（{pr['real']}/{pr['total']}），{len(unreal_items)}条不实",
                "detail": f"典型误判：{sample['description'][:80]}",
                "verdict_samples": [
                    {
                        "verdict": i["verdict"],
                        "description": i["description"][:100],
                        "chapter": i["chapter"],
                        "keywords_matched": list(i["verify_result"]["hits"].keys()),
                    }
                    for i in (unreal_items + uncertain_items)[:2]
                ],
                "recommendation": _suggest_rule_fix(rid, unreal_items, sample),
            }
            suggestions.append(suggestion)

    # 2. 漂移类问题（明显LLM编造）
    drift_items = [r for r in results if r["verdict"] == UNREAL]
    if drift_items:
        suggestions.append({
            "type": "drift",
            "priority": "high",
            "summary": f"发现{drift_items}条漂移缺陷（LLM编造），需加反例或明确判断标准",
            "detail": "; ".join(f"{r['rule_id']}@{r['chapter']}:{r['description'][:40]}" for r in drift_items),
            "recommendation": "在规则末尾增加⚠️判断标准，明确何种情况不算缺陷",
        })

    # 3. 跨章节漏判风险
    # 如果某规则在多个章节出现但只有一个章节有内容，说明章节适用性可能有问题
    chapter_coverage = {}
    for r in results:
        rid = r["rule_id"]
        if rid not in chapter_coverage:
            chapter_coverage[rid] = {}
        if r["chapter"] not in chapter_coverage[rid]:
            chapter_coverage[rid][r["chapter"]] = r["verdict"]
        else:
            # 同一规则同一章节有多条，只记录
            pass

    cross_chapter_issues = []
    for rid, chapters in chapter_coverage.items():
        if len(chapters) > 1:
            verdicts = list(chapters.values())
            if UNCERTAIN in verdicts or UNREAL in verdicts:
                cross_chapter_issues.append({
                    "rule_id": rid,
                    "chapters": chapters,
                    "issue": "该规则多章节触发但存在不实/存疑，可能是章节适用性定义不准确"
                })
    if cross_chapter_issues:
        suggestions.append({
            "type": "cross_chapter",
            "priority": "medium",
            "summary": f"发现{len(cross_chapter_issues)}条跨章节规则存在核实异常",
            "items": cross_chapter_issues,
            "recommendation": "建议检查这些规则的适用章节定义，考虑增加跨章节兜底描述"
        })

    return suggestions


def _suggest_rule_fix(rule_id: str, unreal_items: list, sample: dict) -> str:
    """根据误判案例类型生成具体修复建议"""
    desc = sample.get("description", "")

    if "不存在" in desc or "缺失" in desc:
        return f"建议：在规则中增加正面清单（报告中应包含哪些内容），或增加反例说明常见误判场景"
    elif any(x in desc for x in ["矛盾", "不一致", "不匹配"]):
        return f"建议：在规则中明确判断标准，给出具体数值容差范围（如±5%）"
    elif any(x in desc for x in ["未说明", "未明确", "未给出"]):
        return f"建议：规则应区分'完全没有提到'和'提到了但描述不详细'，前者才算缺陷"
    else:
        return f"建议：检查规则描述是否足够精确，增加具体的判断条件和反面示例"


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def run_self_check(review_id: int) -> dict:
    """
    对指定review_id执行自检，返回完整报告
    """
    # ── 1. 加载DB ──────────────────────────────────────────────────────────
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB不存在: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db = conn.cursor()

    # ── 2. 读取审查信息 ────────────────────────────────────────────────────
    db.execute("""
        SELECT r.*, p.name as project_name, p.file_path, p.file_name
        FROM reviews r
        JOIN projects p ON r.project_id = p.id
        WHERE r.id = ?
    """, (review_id,))
    row = db.fetchone()
    if not row:
        raise ValueError(f"审查不存在: review_id={review_id}")

    review_info = dict(row)

    # ── 3. 读取缺陷列表 ───────────────────────────────────────────────────
    db.execute("""
        SELECT id, rule_id, chapter, severity, description, report_excerpt
        FROM defects
        WHERE review_id = ?
        ORDER BY severity, id
    """, (review_id,))
    defects_raw = [dict(r) for r in db.fetchall()]

    if not defects_raw:
        print("⚠️ 该审查无缺陷记录")
        return {"review_info": review_info, "defects": [], "analysis": {}, "suggestions": []}

    # ── 4. 提取DOCX全文 ───────────────────────────────────────────────────
    docx_path = review_info["file_path"]
    if not docx_path or not Path(docx_path).exists():
        raise FileNotFoundError(f"DOCX文件不存在: {docx_path}")

    print(f"正在加载文档: {docx_path}")
    paragraphs, tables, full_text = load_docx(docx_path)
    print(f"  段落: {len(paragraphs)}, 表格: {len(tables)}, 总字符: {len(full_text):,}")

    # ── 5. 核实每条缺陷 ───────────────────────────────────────────────────
    results = []
    for d in defects_raw:
        rid = d["rule_id"]
        desc = d["description"] or ""
        chapter = d["chapter"] or ""
        report_excerpt = d.get("report_excerpt") or ""

        # 从description提取关键词（逗号/空格分隔的词汇）
        # 策略：提取短小精确的词/词组，避免整句作为关键词
        # 1. 提取具体名词（不带规则前缀）
        kw_raw = re.findall(
            r'DB44/?\d+[^，。；,;\s]{0,20}|'
            r'GB\d+[^，。；,;\s]{0,20}|'
            r'HJ\d+[^，。；,;\s]{0,20}|'
            r'COD|BOD|SS|NH3|NOx|VOC|PM2\.5|PM10|CO|SO2|'
            r'第[一二三四五六七八九十百千万\d]+[章节节表段条次][^，。；,;\s]{0,15}|'
            r'声环境功能区|环境空气质量|土壤|地下水|废气|废水|噪声|固体|危废|监测|预测|评价|标准|限值|指标|排放标准|'
            r'不少于|不超过|大于|小于|等于|符合|不匹配|不一致|矛盾|错误|缺失|未说明|未明确|不合理',
            desc
        )
        # 2. 提取短数值（前后有上下文的数字）
        kw_numbers = re.findall(
            r'(?:废水|废气|浓度|排放|处理|容积|效率|面积|高度|距离|容量|用量)[^\d]*(\d+(?:[.]\d+)?(?:\s*~?\s*\d+(?:[.]\d+)?)?)',
            desc
        )

        keywords = [k.strip() for k in kw_raw if len(k) >= 2 and '】' not in k][:5]

        # 加入 report_excerpt 中的关键词（这是LLM找到的原文，更可信）
        if report_excerpt:
            # 从原文摘录中提取精准片段（去掉报告书特有前缀如【B-005-02】）
            excerpt_clean = re.sub(r'【[^】]+】', '', report_excerpt)
            excerpt_kws = re.findall(
                r'(?:DB44|GB\d+|HJ\d+)[^\s，。；,;]{0,20}'  # 标准号（前后20字）
                r'|(?:第[一二三四五六七八九十百千万\d]+[章节节表段条次])[^\s，。；,;]{0,15}'  # 章节引用
                r'|(?:安居|光明|茅洲|深圳|东莞|中山)[^\s，。；,;]{0,10}'  # 地名
                r'|\d+(?:[.]\d+)?(?:\s*[~-]\s*\d+(?:[.]\d+)?)?(?:mg|m³|t|dB|L|km)?'  # 数值+单位
                r'|(?:不少于|不超过|大于|小于|等于|符合|不一致|矛盾|错误|缺失|未说明|未明确|不合理)',  # 判断词
                excerpt_clean
            )
            excerpt_kws = [k.strip() for k in excerpt_kws if len(k) >= 2 and not any(c in k for c in '【】()（）')]
            keywords = list(dict.fromkeys(keywords + excerpt_kws[:4]))[:8]

        verify_result = verify_defect(keywords, full_text, chapter, desc)

        severity_label = d["severity"] or "一般"
        results.append({
            "defect_id": d["id"],
            "rule_id": rid,
            "chapter": chapter,
            "severity": severity_label,
            "severity_short": SEVERITY_MAP.get(severity_label, "C"),
            "description": desc,
            "keywords_used": keywords,
            "verify_result": verify_result,
            "verdict": verify_result["verdict"],
        })

    # ── 6. 质量分析 ───────────────────────────────────────────────────────
    analysis = analyze_results(results)

    # ── 7. 生成优化建议 ───────────────────────────────────────────────────
    suggestions = generate_optimization_suggestions(results, analysis)

    # ── 8. 写回DB ────────────────────────────────────────────────────────
    now = datetime.now().isoformat()
    for r in results:
        db.execute("""
            INSERT INTO defect_verification_results
            (defect_id, review_id, verdict, hit_rate, verify_keywords,
             verify_context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            r["defect_id"],
            review_id,
            r["verdict"],
            r["verify_result"]["hit_rate"],
            json.dumps(r["keywords_used"], ensure_ascii=False),
            json.dumps(r["verify_result"]["hits"], ensure_ascii=False),
            now,
        ))

    conn.commit()
    conn.close()

    return {
        "review_info": {
            "review_id": review_id,
            "project_name": review_info["project_name"],
            "file_name": review_info["file_name"],
            "status": review_info["status"],
            "total_defects": review_info["total_defects"],
            "type_a": review_info["type_a_count"],
            "type_b": review_info["type_b_count"],
            "type_c": review_info["type_c_count"],
            "completed_at": review_info["completed_at"],
        },
        "defects": results,
        "analysis": analysis,
        "suggestions": suggestions,
    }


def print_report(report: dict):
    """格式化输出报告到终端"""
    ri = report["review_info"]
    a = report["analysis"]
    suggs = report["suggestions"]

    print("\n" + "=" * 90)
    print(f"自检报告 | {ri['project_name']} | review_id={ri['review_id']} | {ri['completed_at']}")
    print("=" * 90)

    print(f"\n【缺陷统计】")
    print(f"  总缺陷: {a['total']} 条  |  属实: {a['real']}  存疑: {a['uncertain']}  不实: {a['unreal']}")
    print(f"  命中率: {a['hit_rate']:.1%}  |  精度: {a['precision']:.1%}  |  漂移率: {a['drift_rate']:.1%}")

    print(f"\n【按严重度】")
    for sev in SEVERITY_ORDER:
        s = a["by_severity"].get(sev, {"total": 0, "real": 0, "unreal": 0})
        if s["total"] > 0:
            hr = s["real"] / s["total"]
            print(f"  {sev}: {s['total']}条  属实{s['real']}  命中率{hr:.0%}")

    print(f"\n【问题规则】(命中率<80%)")
    if a["problem_rules"]:
        for pr in a["problem_rules"]:
            print(f"  {pr['rule_id']}: {pr['hit_rate']:.0%}（{pr['real']}/{pr['total']}）  {pr['verdict']}")
    else:
        print("  无")

    print(f"\n【优化建议】({len(suggs)}条)")
    for i, s in enumerate(suggs, 1):
        print(f"\n  {i}. [{s['type']}] {s['summary']}")
        if s.get("rule_id"):
            print(f"     规则ID: {s['rule_id']}  优先级: {s['priority']}")
        if s.get("detail"):
            print(f"     详情: {s['detail'][:100]}")
        print(f"     建议: {s['recommendation'][:80]}")

    print("\n【缺陷详情】")
    for r in report["defects"]:
        mark = {"属实": "✓", "存疑": "?", "不实": "✗"}[r["verdict"]]
        vr = r["verify_result"]
        print(f"  {mark} {r['rule_id']:12} ch{r['chapter']:20} {r['verdict']:4}  {vr['summary'][:60]}")


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 self_check.py <review_id>")
        print("示例: python3 self_check.py 35")
        sys.exit(1)

    try:
        review_id = int(sys.argv[1])
    except ValueError:
        print(f"无效review_id: {sys.argv[1]}")
        sys.exit(1)

    try:
        report = run_self_check(review_id)
        print_report(report)

        # 保存JSON
        out_path = SCRIPT_DIR / f"self_check_report_{review_id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 报告已保存: {out_path}")

    except FileNotFoundError as e:
        print(f"❌ 文件不存在: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
