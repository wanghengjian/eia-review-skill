#!/usr/bin/env python3
"""
预扫描脚本 - 机械检查层

功能：
1. 扫描所有表格编号及所在章节，建立 table_index
2. 数值预验算（±5%容差）
3. 检测章节引用关系
4. 交叉校验结果注入 LLM prompt

用法：
    python3 pre_scan.py <extract_dir> [--output <output_path>]
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 表格编号正则：表1.4-1, 表1.4-2, 表3.5-31, 表5.3-5 等
TABLE_NUM_PATTERN = re.compile(r'表(\d+(?:[.-]\d+)+)')


def extract_table_numbers(text: str) -> List[str]:
    """从文本中提取所有表格编号（去重）"""
    matches = TABLE_NUM_PATTERN.findall(text)
    # 归一化：保持原始格式
    return list(dict.fromkeys(matches))


def build_table_index(chapters_dict: Dict[str, Dict]) -> Dict[str, List[str]]:
    """
    扫描所有章节，建立 {表格编号: [所在章节号列表]} 索引。

    注意：同一编号的表格可能出现在多个章节（如"表5.3-5"在概述和第五章都出现），
    此时应视为不同表格（复用在不同章节）。
    """
    index: Dict[str, List[str]] = {}
    for ch_num, ch_data in chapters_dict.items():
        content = ch_data.get('content', '')
        for table_num in extract_table_numbers(content):
            if table_num not in index:
                index[table_num] = []
            if ch_num not in index[table_num]:
                index[table_num].append(ch_num)
    return index


def find_referenced_tables(content: str) -> Dict[str, str]:
    """
    在正文内容中，找出"引用了某表"的位置，返回 {表格编号: 引用上下文}.

    识别模式：
    - "见表X.X-X"
    - "如表X.X-X所示"
    - "表X.X-X中列出了..."
    """
    references: Dict[str, str] = {}
    # 匹配"见表"或"如表"后跟表格编号
    patterns = [
        r'(?:见|如|依据|根据|按|按照|参见?|参照|按表|按表所示|表[^，。,\n]{0,20})',
    ]
    for m in TABLE_NUM_PATTERN.finditer(content):
        start = max(0, m.start() - 15)
        end = min(len(content), m.end() + 15)
        context = content[start:end].strip()
        table_num = m.group(1)
        if table_num not in references:
            references[table_num] = context
    return references


def verify_numeric_consistency(chapters_dict: Dict[str, Dict]) -> List[Dict[str, Any]]:
    """
    数值预验算。

    识别并验算以下类型的数值一致性：
    1. 给排水总量：各分项之和 vs 声称总量
    2. 蒸汽/冷凝水量：分项之和 vs 声称总量
    3. 建筑面积：A栋 + B栋 + A2栋 vs 声称总量
    4. 环保投资：分项之和 vs 声称总量
    5. 废气/废水排放量：分项之和 vs 声称总量

    差异 ≤ ±5% 视为一致。
    """
    results: List[Dict[str, Any]] = []

    # 配置各类型的识别规则
    rules = [
        {
            "type": "建筑面积",
            "keywords": ["建筑面积", "建筑面", "用地面积"],
            "total_pattern": r'(?:总(?:计|建筑)?(?:用?地)?(?:面积)?|用地面积)[：:]\s*([0-9]+\.?[0-9]*)\s*(?:m²|m2|平方米|㎡)',
            "item_pattern": r'([A-Z0-9]+栋)[^0-9]{0,20}?([0-9]+\.?[0-9]*)',
            "max_item_value": 50000,  # m²，合理上限
            "unit": "m²",
        },
        {
            "type": "给排水",
            "keywords": ["给排水", "用水量", "新鲜水量", "日用水量"],
            "total_pattern": r'(?:新鲜|生产|总)(?:用?水|新鲜水)(?:量)?[：:]\s*([0-9]+\.?[0-9]*)\s*(?:m³|m3|吨|d)',
            "item_pattern": r'([^\s：:]{2,8})[：:]\s*([0-9]+\.?[0-9]*)\s*(?:m³|m3|吨|d)',
            "max_item_value": 5000,  # m³/d，合理上限
            "unit": "m³/d",
        },
        {
            "type": "蒸汽/冷凝水",
            "keywords": ["蒸汽.*冷凝|冷凝.*蒸汽|凝结.*蒸汽|供热.*冷凝", "冷凝水"],
            "total_pattern": r'(?:总蒸汽|总冷凝|蒸汽.*?:|冷凝.*?:)\s*([0-9]+\.?[0-9]*)\s*(?:m³|m3|吨|d)',
            "item_pattern": r'([A-Z0-9]+栋)[^0-9]{0,20}?([0-9]+\.?[0-9]*)\s*(?:m³|m3|吨|d)',
            "max_item_value": 500,
            "unit": "m³/d",
        },
        {
            "type": "环保投资",
            "keywords": ["环保投资", "环境保护投资", "治理投资"],
            "total_pattern": r'环保(?:保护)?投资[^0-9]{0,30}?([0-9]+\.?[0-9]*)\s*(?:万元|万)(?!\s*[a-zA-Z])',
            "item_pattern": r'(?:废水|废气|噪声|固废|土壤|监测|其他)[^0-9]{0,20}?([0-9]+\.?[0-9]*)\s*(?:万元|万)',
            "max_item_value": 5000,  # 万元，合理上限
            "unit": "万元",
        },
    ]

    for ch_num, ch_data in chapters_dict.items():
        content = ch_data.get('content', '')

        for rule in rules:
            if not any(kw in content for kw in rule['keywords']):
                continue

            # 提取声称总量
            total_match = re.search(rule['total_pattern'], content)
            if not total_match:
                continue
            try:
                total_val = float(re.sub(r'[^\d.]', '', total_match.group(1)))
            except (ValueError, AttributeError):
                continue

            if total_val <= 0:
                continue

            # 提取分项（找所有数值，过滤不合理大值）
            max_val = rule.get("max_item_value", float('inf'))
            items: List[Tuple[str, float]] = []
            for item_m in re.finditer(rule['item_pattern'], content):
                try:
                    if item_m.lastindex is None or item_m.lastindex < 2:
                        continue
                    name = item_m.group(1).strip()
                    val_str = re.sub(r'[^\d.]', '', item_m.group(2))
                    if not val_str:
                        continue
                    val = float(val_str)
                    if 0 < val <= max_val:
                        items.append((name, val))
                except (ValueError, AttributeError, IndexError):
                    continue

            if len(items) < 2:
                continue

            # 计算分项和
            sum_val = sum(v for _, v in items)

            # 计算相对差异
            diff_pct = abs(sum_val - total_val) / total_val * 100 if total_val > 0 else 0
            is_consistent = diff_pct <= 5.0

            if not is_consistent:
                results.append({
                    "chapter": ch_num,
                    "type": rule['type'],
                    "total_value": total_val,
                    "sum_of_items": sum_val,
                    "items": items,
                    "diff_pct": round(diff_pct, 2),
                    "within_tolerance": False,
                    "status": "矛盾" if diff_pct > 5 else "一致",
                })

    return results


def check_table_reference_validity(
    chapters_dict: Dict[str, Dict],
    table_index: Dict[str, List[str]]
) -> List[Dict[str, Any]]:
    """
    检查表格引用的有效性：
    - 正文引用了某表编号，但该编号在全文中不存在 → 引用失效
    - 正文引用了某表编号，该编号存在但不在当前章节 → 跨章引用
    """
    issues: List[Dict[str, Any]] = []

    for ch_num, ch_data in chapters_dict.items():
        content = ch_data.get('content', '')
        references = find_referenced_tables(content)

        for table_num, context in references.items():
            if table_num not in table_index:
                issues.append({
                    "chapter": ch_num,
                    "table_num": table_num,
                    "issue_type": "TABLE_NOT_FOUND",
                    "context": context,
                    "description": f"正文引用了「表{table_num}」，但该编号在全文中未找到对应表格"
                })
            else:
                # 表格存在，检查是否跨章引用
                chapters_with_table = table_index[table_num]
                if ch_num not in chapters_with_table:
                    # 跨章引用（可能是问题也可能不是）
                    issue = {
                        "chapter": ch_num,
                        "table_num": table_num,
                        "issue_type": "CROSS_CHAPTER_REFERENCE",
                        "actual_chapters": chapters_with_table,
                        "context": context,
                        "description": f"正文引用了「表{table_num}」，该表实际在第{'/'.join(chapters_with_table)}章，本章为第{ch_num}章"
                    }
                    # 只有当引用声称"本章有表X"时才记为问题
                    if any(kw in context for kw in ['本章', '本章表', '本章列', '本章示']):
                        issue["issue_type"] = "CLAIMED_LOCAL_BUT_ACTUAL_GLOBAL"
                        issue["description"] = f"正文声称本章有「表{table_num}」，但该表实际在第{'/'.join(chapters_with_table)}章"
                        issues.append(issue)

    return issues


def generate_pre_scan_report(
    extract_dir: Path,
    output_path: Optional[Path] = None
) -> Dict[str, Any]:
    """
    对提取目录运行完整预扫描，生成报告。

    Args:
        extract_dir: extract.py 的输出目录（含 chapters/ 和 full_text.txt）
        output_path: 可选，JSON 报告输出路径

    Returns:
        预扫描报告字典
    """
    # 读取 full_text（支持多种命名格式）
    candidates = list((extract_dir / "extract").glob("*_full_text.txt"))
    if not candidates:
        return {"error": f"未找到 full_text.txt：{extract_dir / 'extract'}"}
    full_text_file = candidates[0]

    with open(full_text_file, encoding='utf-8') as f:
        full_text = f.read()

    # 读取 chapters
    chapters_dir = extract_dir / "extract" / "chapters"
    chapters_dict: Dict[str, Dict] = {}
    if chapters_dir.exists():
        for f in chapters_dir.glob("chapter_*.txt"):
            ch_num = f.stem.replace("chapter_", "")
            chapters_dict[ch_num] = {
                "content": f.read_text(encoding='utf-8'),
                "file": str(f)
            }

    # 1. 表格编号索引
    table_index = build_table_index(chapters_dict)

    # 2. 数值验算
    numeric_results = verify_numeric_consistency(chapters_dict)

    # 3. 表格引用有效性
    table_issues = check_table_reference_validity(chapters_dict, table_index)

    # 4. 生成 LLM prompt 注入文本
    llm_injection = generate_llm_injection(
        table_index, numeric_results, table_issues, chapters_dict,
    )

    report = {
        "table_index": table_index,
        "table_count": len(table_index),
        "numeric_results": numeric_results,
        "table_issues": table_issues,
        "llm_injection": llm_injection,
        "metadata": {
            "extract_dir": str(extract_dir),
            "chapter_count": len(chapters_dict),
            "table_count": len(table_index),
        }
    }

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def generate_llm_injection(
    table_index: Dict[str, List[str]],
    numeric_results: List[Dict[str, Any]],
    table_issues: List[Dict[str, Any]],
    chapters_dict: Dict[str, Dict],
) -> str:
    """
    生成注入到 LLM prompt 的预扫描结果文本。
    """
    lines = ["\n## 已知信息（预扫描结果）\n"]

    # 1. 表格编号索引（按章节分组，列出关键表）
    lines.append("**表格编号索引（预扫描）：**")
    if table_index:
        # 按章节号排序
        by_chapter: Dict[str, List[str]] = {}
        for tbl, chs in table_index.items():
            for ch in chs:
                if ch not in by_chapter:
                    by_chapter[ch] = []
                by_chapter[ch].append(tbl)

        for ch_num in sorted(by_chapter.keys()):
            tables = sorted(by_chapter[ch_num])
            lines.append(f"- 第{ch_num}章：{', '.join(tables)}")
    else:
        lines.append("- （未检测到表格编号）")

    # 2. 数值验算结果
    lines.append("\n**数值预验算结果（±5%以内视为一致）：**")
    if numeric_results:
        for r in numeric_results:
            items_str = " + ".join([f"{n}={v}" for n, v in r['items'][:5]])
            status_emoji = "❌" if not r['within_tolerance'] else "✅"
            lines.append(
                f"- {status_emoji} 第{r['chapter']}章 {r['type']}："
                f"声称={r['total_value']}，验算={r['sum_of_items']}，差异={r['diff_pct']}%"
            )
    else:
        lines.append("- 未检测到数值矛盾")

    # 3. 表格引用问题
    lines.append("\n**表格引用问题（预扫描）：**")
    if table_issues:
        for issue in table_issues[:10]:  # 最多10条
            lines.append(
                f"- ❌ 第{issue['chapter']}章：{issue['description']} "
                f"（上下文：「{issue['context'][:30]}...」）"
            )
    else:
        lines.append("- 未发现引用失效问题")

    # 4. 关键警告
    warnings = []
    for issue in table_issues:
        if issue['issue_type'] in ('TABLE_NOT_FOUND', 'CLAIMED_LOCAL_BUT_ACTUAL_GLOBAL'):
            warnings.append(f"第{issue['chapter']}章引用「表{issue['table_num']}」{issue['description']}")

    if warnings:
        lines.append("\n**⚠️ 关键警告：**")
        for w in warnings[:5]:
            lines.append(f"- {w}")

    lines.append("\n*以上为机械预扫描结果，供参考。如预扫描与人工判断不符，以人工判断为准。*")

    return "\n".join(lines)


def load_pre_scan_report(report_path: Path) -> Dict[str, Any]:
    """加载已存在的预扫描报告"""
    with open(report_path, encoding='utf-8') as f:
        return json.load(f)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='预扫描：表格编号索引 + 数值验算 + 引用检查')
    parser.add_argument('extract_dir', help='extract输出目录')
    parser.add_argument('--output', '-o', help='JSON报告输出路径')
    args = parser.parse_args()

    extract_dir = Path(args.extract_dir)
    output_path = Path(args.output) if args.output else None

    report = generate_pre_scan_report(extract_dir, output_path)

    if "error" in report:
        print(f"错误：{report['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"预扫描完成：{report['metadata']['chapter_count']}章节，"
          f"{report['metadata']['table_count']}个表格编号，"
          f"{len(report['numeric_results'])}条数值矛盾，"
          f"{len(report['table_issues'])}条引用问题")

    if output_path:
        print(f"报告已保存：{output_path}")
    else:
        print("\n--- LLM注入文本 ---")
        print(report['llm_injection'])
