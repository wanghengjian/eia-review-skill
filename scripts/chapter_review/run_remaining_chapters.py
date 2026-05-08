#!/usr/bin/env python3
"""
续审脚本 - 用于中断后继续审查未完成的章节

用法：
    python3 run_remaining_chapters.py <findings_dir> [--concurrent 3] [--timeout 120]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent / "utils"))

from process_chapters_v2 import review_chapters_async


def find_remaining_chapters(findings_dir: Path, all_chapters: List[str]) -> List[str]:
    """找出未完成审查的章节"""
    completed = set()

    for f in findings_dir.glob("findings_*.json"):
        # 从文件名提取章节号
        # 格式: findings_001_xxx.json
        name = f.stem
        parts = name.split("_")
        if len(parts) >= 2 and parts[1].isdigit():
            completed.add(parts[1])

    remaining = [c for c in all_chapters if c not in completed]
    return sorted(remaining)


def main():
    parser = argparse.ArgumentParser(description='续审脚本 - 继续审查未完成的章节')
    parser.add_argument('findings_dir', help='findings目录')
    parser.add_argument('--concurrent', '-n', type=int, default=3, help='并发数（默认3）')
    parser.add_argument('--timeout', '-t', type=int, default=120, help='LLM超时秒数（默认120）')

    args = parser.parse_args()

    findings_dir = Path(args.findings_dir)

    if not findings_dir.exists():
        print(f"错误: 目录不存在: {findings_dir}")
        sys.exit(1)

    # 查找extract目录
    extract_dir = findings_dir.parent / "extract"
    if not extract_dir.exists():
        extract_dir = findings_dir.parent.parent / "extract"

    if not extract_dir.exists():
        print(f"错误: extract目录不存在")
        sys.exit(1)

    # 发现所有章节
    chapters_dir = extract_dir / "chapters"
    all_chapters = []
    for f in chapters_dir.glob("chapter_*.txt"):
        num = f.stem.replace("chapter_", "")
        all_chapters.append(num)
    all_chapters.sort()

    # 找出未完成的章节
    remaining = find_remaining_chapters(findings_dir, all_chapters)

    if not remaining:
        print("所有章节已完成审查，无需续审")
        sys.exit(0)

    print(f"发现 {len(remaining)} 个未完成章节: {', '.join(remaining)}")
    print(f"并发数: {args.concurrent}, 超时: {args.timeout}秒")
    print()

    # 执行续审
    print("开始续审...")
    results = asyncio.run(review_chapters_async(
        extract_dir, findings_dir.parent,
        remaining, args.concurrent, args.timeout
    ))

    # 统计
    total_findings = 0
    for num, result in results.items():
        if "error" not in result:
            total_findings += len(result.get("findings", []))

    print(f"\n续审完成: {total_findings} 个新增发现")
    print(f"结果保存在: {findings_dir}")


if __name__ == "__main__":
    main()
