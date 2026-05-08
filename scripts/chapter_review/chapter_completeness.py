#!/usr/bin/env python3
"""
章节完整性审查脚本

功能：
- 检查各标准章节是否存在
- 检查各章节是否有实质性内容
- 检查深圳地标要求的"总量控制"专节

用法：
    python3 chapter_completeness.py <extract_dir> <output_dir>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Set


# 标准章节结构
STANDARD_CHAPTERS = {
    "001": {"name": "概述", "required": True},
    "002": {"name": "第一章 总则", "required": True},
    "003": {"name": "第二章 项目概况", "required": True},
    "004": {"name": "第三章 工程分析", "required": True},
    "005": {"name": "第四章 环境现状调查", "required": True},
    "006": {"name": "第五章 环境影响预测", "required": True},
    "007": {"name": "第六章 环境保护措施", "required": True},
    "008": {"name": "第七章 环境风险评价", "required": True},
    "009": {"name": "第八章 经济损益分析", "required": True},
    "010": {"name": "第九章 环境管理监测", "required": True},
    "011": {"name": "第十章 结论与建议", "required": True},
    "012": {"name": "附件与附表", "required": False},
}

# 深圳地标特殊要求
SHENZHEN_SPECIAL_REQUIREMENTS = {
    "total_control": {
        "name": "总量控制专节",
        "keywords": ["总量控制", "污染物总量", "排放总量", "总量指标"],
        "required": True,
        "note": "根据DB4403/T 548-2024，深圳地标要求设置总量控制专节或独立章节"
    }
}


def check_chapter_completeness(chapters_dir: Path) -> Dict:
    """检查章节完整性"""
    results = {
        "status": "complete",
        "missing_chapters": [],
        "empty_chapters": [],
        "chapters_found": {},
        "total_control_check": {
            "found": False,
            "location": "",
            "status": "pass"
        },
        "summary": ""
    }

    found_chapters = {}

    # 检查各章节文件
    for chapter_num, chapter_info in STANDARD_CHAPTERS.items():
        chapter_file = chapters_dir / f"chapter_{chapter_num}.txt"

        if not chapter_file.exists():
            if chapter_info["required"]:
                results["missing_chapters"].append(chapter_num)
                results["status"] = "incomplete"
        else:
            content = chapter_file.read_text(encoding='utf-8')
            content_stripped = content.strip()

            # 检查是否有实质性内容（至少50个字符）
            if len(content_stripped) < 50:
                results["empty_chapters"].append(chapter_num)
                found_chapters[chapter_num] = {
                    "name": chapter_info["name"],
                    "status": "empty",
                    "char_count": len(content_stripped)
                }
            else:
                found_chapters[chapter_num] = {
                    "name": chapter_info["name"],
                    "status": "ok",
                    "char_count": len(content_stripped),
                    "first_line": content_stripped[:100]
                }

    results["chapters_found"] = found_chapters

    # 检查总量控制专节（深圳地标特殊要求）
    total_control_found = False
    total_control_location = ""

    for chapter_num, chapter_data in found_chapters.items():
        if chapter_data["status"] == "ok":
            chapter_file = chapters_dir / f"chapter_{chapter_num}.txt"
            content = chapter_file.read_text(encoding='utf-8').lower()

            for keyword in SHENZHEN_SPECIAL_REQUIREMENTS["total_control"]["keywords"]:
                if keyword.lower() in content:
                    total_control_found = True
                    total_control_location = f"章节 {chapter_num} ({STANDARD_CHAPTERS[chapter_num]['name']})"
                    break

        if total_control_found:
            break

    results["total_control_check"] = {
        "found": total_control_found,
        "location": total_control_location,
        "status": "pass" if total_control_found else "warning",
        "note": SHENZHEN_SPECIAL_REQUIREMENTS["total_control"]["note"]
    }

    if not total_control_found:
        results["status"] = "warning"

    # 生成总结
    if results["status"] == "complete":
        results["summary"] = f"章节完整性检查通过，共 {len(found_chapters)} 个章节"
    elif results["status"] == "incomplete":
        results["summary"] = f"章节不完整，缺少 {len(results['missing_chapters'])} 个必填章节"
    else:
        results["summary"] = f"章节完整性有警告"

    return results


def generate_completeness_report(results: Dict, project_info: Dict) -> str:
    """生成完整性检查报告"""
    report_lines = [
        "# 章节完整性审核报告",
        "",
        "## 基本信息",
        "",
        f"- **项目名称**: {project_info.get('project_name', '未知')}",
        f"- **建设单位**: {project_info.get('company', '未知')}",
        f"- **评价等级**: {project_info.get('evaluation_level', '未知')}",
        "",
        "## 审核结果",
        "",
        f"**整体状态**: {'✅ 通过' if results['status'] == 'complete' else '⚠️ 有警告' if results['status'] == 'warning' else '❌ 不通过'}",
        "",
    ]

    # 缺失章节
    if results["missing_chapters"]:
        report_lines.extend([
            "### 缺失章节",
            "",
            "| 章节编号 | 章节名称 |",
            "|---------|---------|"
        ])
        for num in results["missing_chapters"]:
            report_lines.append(f"| {num} | {STANDARD_CHAPTERS[num]['name']} |")
        report_lines.append("")

    # 空章节
    if results["empty_chapters"]:
        report_lines.extend([
            "### 空章节（内容不足）",
            "",
            "| 章节编号 | 章节名称 | 字符数 |",
            "|---------|---------|--------|"
        ])
        for num in results["empty_chapters"]:
            info = results["chapters_found"][num]
            report_lines.append(f"| {num} | {info['name']} | {info['char_count']} |")
        report_lines.append("")

    # 章节列表
    report_lines.extend([
        "### 章节列表",
        "",
        "| 章节编号 | 章节名称 | 状态 | 字符数 |",
        "|---------|---------|------|--------|"
    ])

    for num, info in sorted(results["chapters_found"].items()):
        status_icon = "✅" if info["status"] == "ok" else "⚠️"
        report_lines.append(f"| {num} | {info['name']} | {status_icon} | {info.get('char_count', 0)} |")

    report_lines.append("")

    # 深圳地标特殊要求
    tc_check = results["total_control_check"]
    report_lines.extend([
        "### 深圳地标特殊要求检查",
        "",
        f"- **总量控制专节**: {'✅ 已设置' if tc_check['found'] else '⚠️ 未找到'}",
        f"- **位置**: {tc_check['location'] if tc_check['location'] else '无'}",
        f"- **说明**: {tc_check['note']}",
        "",
        "**注**: 根据DB4403/T 548-2024《环境影响评价技术审查规则》，深圳地区的环境影响报告书应设置总量控制专节或独立章节。",
        ""
    ])

    # 总结
    report_lines.extend([
        "## 审核总结",
        "",
        results["summary"],
        ""
    ])

    return '\n'.join(report_lines)


def main():
    parser = argparse.ArgumentParser(description='章节完整性审查工具')
    parser.add_argument('extract_dir', help='extract输出目录')
    parser.add_argument('--output', '-o', help='输出目录（默认与extract_dir相同）')

    args = parser.parse_args()

    extract_dir = Path(args.extract_dir)
    if not extract_dir.exists():
        print(f"错误: 目录不存在: {extract_dir}", file=sys.stderr)
        sys.exit(1)

    chapters_dir = extract_dir / "chapters"
    if not chapters_dir.exists():
        print(f"错误: 章节目录不存在: {chapters_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output) if args.output else extract_dir / "findings"
    findings_dir = output_dir / "章节完整性"
    findings_dir.mkdir(parents=True, exist_ok=True)

    print("正在检查章节完整性...")

    # 加载项目信息
    info_file = extract_dir / "项目信息.json"
    project_info = {}
    if info_file.exists():
        project_info = json.loads(info_file.read_text(encoding='utf-8'))

    # 检查完整性
    results = check_chapter_completeness(chapters_dir)

    # 保存结果JSON
    result_file = findings_dir / "check_result.json"
    result_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')

    # 生成报告
    report = generate_completeness_report(results, project_info)
    report_file = findings_dir / "完整性报告.md"
    report_file.write_text(report, encoding='utf-8')

    print(f"\n章节完整性检查完成！")
    print(f"状态: {results['status']}")
    print(f"发现章节: {len(results['chapters_found'])}")
    if results['missing_chapters']:
        print(f"缺失章节: {', '.join(results['missing_chapters'])}")
    if results['empty_chapters']:
        print(f"空章节: {', '.join(results['empty_chapters'])}")
    print(f"\n报告已保存: {report_file}")


if __name__ == "__main__":
    main()
