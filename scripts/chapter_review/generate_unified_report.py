#!/usr/bin/env python3
"""
统一报告生成脚本

功能：
- 汇总所有章节审查结果
- 去重并分级
- 生成统一的审查报告

用法：
    python3 generate_unified_report.py <findings_dir> <project_info_file> <output_file>
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

# 添加 utils 目录到路径，导入规则加载器
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from review_rules_loader import _load_rules_text


# 规则文本缓存（按 rule_id 索引审核步骤）
_rules_check_steps_cache: Dict[str, List[str]] = {}


def _get_check_steps(rule_id: str) -> List[str]:
    """从规则库中获取指定规则的审核步骤列表。带缓存。"""
    global _rules_check_steps_cache
    if _rules_check_steps_cache:
        return _rules_check_steps_cache.get(rule_id, [])

    # 首次调用：解析整个规则库并建立缓存
    try:
        text = _load_rules_text()
    except Exception:
        return []

    # 按 ### B-xxx 或 ### C-xxx 分割规则块
    blocks = re.split(r'\n(?=### [BCAS]-\d+)', text)

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # 提取规则ID
        m = re.match(r'^### ([BCAS]-\d+)', block)
        if not m:
            continue
        rid = m.group(1)

        # 提取审核步骤
        steps_match = re.search(
            r'\*\*审核步骤\*\*[:：]\s*\n((?:\s*\d+[.、].+\n?)+)',
            block
        )
        if not steps_match:
            _rules_check_steps_cache[rid] = []
            continue

        steps_text = steps_match.group(1)
        # 提取各步骤内容（去掉编号前缀）
        steps = re.findall(r'(?:^|\n)\s*\d+[.、]\s*(.+)', steps_text)
        # 清理空白
        steps = [s.strip() for s in steps if s.strip()]
        _rules_check_steps_cache[rid] = steps

    return _rules_check_steps_cache.get(rule_id, [])


def load_findings(findings_dir: Path) -> Tuple[List[Dict], Dict]:
    """加载所有findings"""
    all_findings = []
    summary = {
        "total_files": 0,
        "chapters_reviewed": [],
        "errors": []
    }

    if not findings_dir.exists():
        return [], summary

    for f in findings_dir.glob("findings_*.json"):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            summary["total_files"] += 1

            chapter_num = data.get("chapter_num", f.stem)
            chapter_name = data.get("chapter_name", chapter_num)
            summary["chapters_reviewed"].append(f"{chapter_num} {chapter_name}")

            findings = data.get("findings", [])
            for finding in findings:
                finding["source_chapter"] = chapter_num
                finding["source_chapter_name"] = chapter_name
                finding["source_file"] = f.name
            all_findings.extend(findings)

            if data.get("errors"):
                summary["errors"].extend(data["errors"])

        except Exception as e:
            summary["errors"].append(f"{f.name}: {str(e)}")

    # 按章节排序
    summary["chapters_reviewed"].sort()

    return all_findings, summary


def deduplicate_and_classify(findings: List[Dict]) -> Dict[str, List[Dict]]:
    """去重并按严重程度分级"""
    # 按规则ID和描述去重
    seen = set()
    unique = []

    for finding in findings:
        key = f"{finding.get('rule_id', '')}_{finding.get('description', '')[:100]}"
        if key not in seen:
            seen.add(key)
            unique.append(finding)

    # 分级
    classified = {
        "high": [],   # 重大缺陷
        "medium": [], # 较大缺陷
        "low": []     # 一般缺陷
    }

    for finding in unique:
        severity = finding.get("severity", "low")
        if severity == "high":
            classified["high"].append(finding)
        elif severity == "medium":
            classified["medium"].append(finding)
        else:
            classified["low"].append(finding)

    # 每类内部按章节排序
    for level in classified:
        classified[level].sort(key=lambda x: x.get("source_chapter", "999"))

    return classified


def _format_finding(f: Dict) -> str:
    """格式化单个发现为 Markdown 列表项"""
    rule_id = f.get("rule_id", "")
    source = f.get("source_chapter_name", f.get("source_chapter", ""))
    situation = f.get("situation", "")
    description = f.get("description", "")
    legal_basis = f.get("legal_basis", "")
    suggestion = f.get("suggestion", "")

    lines = [f"- **{rule_id}** [{source}]"
             + (f" [情形] {situation}" if situation else "")]

    if description:
        lines[0] += f"\n  - **问题**：{description}"
    if legal_basis:
        lines.append(f"  - **依据**：{legal_basis}")
    if suggestion:
        lines.append(f"  - **建议**：{suggestion}")

    return "\n".join(lines)


def group_findings_by_chapter(findings: List[Dict]) -> Dict[str, Dict]:
    """按章节分组，每章节内按严重程度分级"""
    chapter_groups = {}

    for f in findings:
        ch = f.get("source_chapter", "unknown")
        if ch not in chapter_groups:
            chapter_groups[ch] = {
                "name": f.get("source_chapter_name", ch),
                "high": [],
                "medium": [],
                "low": []
            }

        severity = f.get("severity", "low")
        if severity == "high":
            chapter_groups[ch]["high"].append(f)
        elif severity == "medium":
            chapter_groups[ch]["medium"].append(f)
        else:
            chapter_groups[ch]["low"].append(f)

    # 按章节编号排序
    sorted_chapters = {}
    for ch in sorted(chapter_groups.keys()):
        sorted_chapters[ch] = chapter_groups[ch]

    return sorted_chapters


def _format_finding_compact(f: Dict) -> str:
    """格式化单个发现为紧凑列表项（含审核步骤）"""
    rule_id = f.get("rule_id", "")
    title = f.get("title", "")
    description = f.get("description", "")
    suggestion = f.get("suggestion", "")
    location = f.get("location", "")

    lines = [f"- **{rule_id}** {title}"]
    if location:
        lines[0] += f"（{location}）"
    if description:
        lines.append(f"  - 问题：{description}")

    # 追加审核步骤（从规则库查出）
    if rule_id and rule_id not in ("通用", ""):
        steps = _get_check_steps(rule_id)
        if steps:
            steps_preview = " | ".join(steps[:3])
            if len(steps) > 3:
                steps_preview += f" | ...（共{len(steps)}步）"
            lines.append(f"  - 审核：{steps_preview}")

    if suggestion:
        lines.append(f"  - 建议：{suggestion}")

    return "\n".join(lines)


def generate_report(
    project_info: Dict,
    classified_findings: Dict[str, List[Dict]],
    summary: Dict,
    completeness_result: Dict = None
) -> str:
    """生成统一格式的审查报告（按章节组织缺陷清单）"""

    high = classified_findings["high"]
    medium = classified_findings["medium"]
    low = classified_findings["low"]

    high_count = len(high)
    medium_count = len(medium)
    low_count = len(low)

    # 判断结论
    if high_count > 0:
        verdict = "❌ 不通过"
        verdict_reason = f"存在 {high_count} 项重大缺陷，属于不予批准情形"
    elif medium_count > 0:
        verdict = "⚠️ 修改"
        verdict_reason = f"存在 {medium_count} 项较大缺陷，需要补充完善"
    else:
        verdict = "✅ 通过"
        verdict_reason = "未发现重大缺陷，可以上报"

    # 项目基本信息
    project_name = project_info.get("project_name", "（未提取到）")
    company = project_info.get("company", "（未提取到）")
    construction_nature = project_info.get("construction_nature", "（未提取到）")
    evaluation_level = project_info.get("evaluation_level", "（未提取到）")
    location = project_info.get("location", "（未提取到）")
    total_investment = project_info.get("total_investment", "（未提取到）")
    environmental_investment = project_info.get("environmental_investment", "（未提取到）")
    construction_content_scale = project_info.get("construction_content_scale", "（未提取到）")

    # 按章节分组
    all_findings = high + medium + low
    chapter_groups = group_findings_by_chapter(all_findings)

    # 章节缺陷汇总行
    chapter_stats_lines = []
    for ch, data in chapter_groups.items():
        h = len(data["high"])
        m = len(data["medium"])
        l = len(data["low"])
        badge = ""
        if h > 0:
            badge += f" 🔴{h}"
        if m > 0:
            badge += f" 🟡{m}"
        if l > 0:
            badge += f" ⚪{l}"
        chapter_stats_lines.append(f"- **{data['name']}**：{h + m + l} 个问题{badge}")

    chapters_section = "\n".join(chapter_stats_lines) if chapter_stats_lines else "-（无章节统计）"

    # 按章节构建缺陷清单
    defect清单_lines = []
    for ch, data in chapter_groups.items():
        name = data["name"]
        ch_high = data["high"]
        ch_medium = data["medium"]
        ch_low = data["low"]

        lines = [f"### {name}"]

        # 重大缺陷
        if ch_high:
            lines.append(f"🔴 重大缺陷（{len(ch_high)}项）：")
            for f in ch_high:
                lines.append(_format_finding_compact(f))
            lines.append("")

        # 较大缺陷
        if ch_medium:
            lines.append(f"🟡 较大缺陷（{len(ch_medium)}项）：")
            for f in ch_medium:
                lines.append(_format_finding_compact(f))
            lines.append("")

        # 一般缺陷
        if ch_low:
            lines.append(f"⚪ 一般缺陷（{len(ch_low)}项）：")
            for f in ch_low:
                lines.append(_format_finding_compact(f))
            lines.append("")

        defect清单_lines.append("\n".join(lines))

    defects_section = "\n\n---\n\n".join(defect清单_lines)

    # 完整性状态
    completeness_status = "✅ 通过" if completeness_result and completeness_result.get("status") == "complete" else "⚠️ 有警告"
    total_control_status = "✅ 已设置" if completeness_result and completeness_result.get("total_control_check", {}).get("found") else "⚠️ 未找到"

    # 统计摘要
    b_rule_ids = set(f.get("rule_id", "") for f in high)
    c_rule_ids = set(f.get("rule_id", "") for f in medium)
    a_rule_ids = set(f.get("rule_id", "") for f in low)

    report = f"""# 环境影响报告书审查报告

**项目名称**：{project_name}
**建设单位**：{company}
**建设性质**：{construction_nature}
**环评类别**：{evaluation_level}
**建设地点**：{location}
**总投资**：{total_investment}
**环保投资**：{environmental_investment}
**建设内容/规模**：{construction_content_scale}

---

## 一、项目情况

本项目位于 {location}，为 {construction_nature}，总投资 {total_investment}，其中环保投资 {environmental_investment}。

## 二、章节完整性检查

- **章节完整性**：{completeness_status}
- **工程内容与分析**：{total_control_status}
- **审核章节数**：{len(chapter_groups)} 个

### 问题章节统计

{chapters_section}

---

## 三、缺陷清单（按章节）

{defects_section}

---

## 审查结论

**{verdict}**

{verdict_reason}

---

## 统计摘要

- 审核章节数：{len(chapter_groups)} 个
- 问题总数：{high_count + medium_count + low_count}（重大 {high_count} / 较大 {medium_count} / 一般 {low_count}）
- B类规则：{len(b_rule_ids)} 条 / C类规则：{len(c_rule_ids)} 条 / A类规则：{len(a_rule_ids)} 条
- 审核时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

"""

    return report


def main():
    parser = argparse.ArgumentParser(description='统一报告生成工具')
    parser.add_argument('findings_dir', help='findings目录')
    parser.add_argument('project_info', help='项目信息JSON文件')
    parser.add_argument('output_file', help='输出文件路径')

    args = parser.parse_args()

    findings_dir = Path(args.findings_dir)
    project_info_file = Path(args.project_info)
    output_file = Path(args.output_file)

    if not findings_dir.exists():
        print(f"错误: findings目录不存在: {findings_dir}", file=sys.stderr)
        sys.exit(1)

    # 加载项目信息
    project_info = {}
    if project_info_file.exists():
        project_info = json.loads(project_info_file.read_text(encoding='utf-8'))

    # 加载章节完整性检查结果
    completeness_result = None
    completeness_file = findings_dir / "章节完整性" / "check_result.json"
    if completeness_file.exists():
        completeness_result = json.loads(completeness_file.read_text(encoding='utf-8'))

    # 加载所有findings
    print("正在加载审查结果...")
    findings, summary = load_findings(findings_dir)
    print(f"共加载 {len(findings)} 个发现（来自 {summary['total_files']} 个文件）")

    # 去重分级
    print("正在去重分级...")
    classified = deduplicate_and_classify(findings)
    print(f"  🔴 重大: {len(classified['high'])}")
    print(f"  🟡 较大: {len(classified['medium'])}")
    print(f"  ⚪ 一般: {len(classified['low'])}")

    # 生成报告
    print("正在生成报告...")
    report = generate_report(project_info, classified, summary, completeness_result)

    # 保存
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(report, encoding='utf-8')

    print(f"\n报告已保存: {output_file}")

    # 打印结论
    high_count = len(classified["high"])
    if high_count > 0:
        print(f"\n❌ 结论：不通过（存在 {high_count} 项重大缺陷）")
    elif len(classified["medium"]) > 0:
        print(f"\n⚠️ 结论：修改（存在 {len(classified['medium'])} 项较大缺陷）")
    else:
        print(f"\n✅ 结论：通过")


if __name__ == "__main__":
    main()
