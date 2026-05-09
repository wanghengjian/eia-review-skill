#!/usr/bin/env python3
"""
逐章审查引擎 v2

功能：
- 并发审查多个章节（默认3并发）
- 自动分块处理长内容
- 支持中断恢复

用法：
    python3 process_chapters_v2.py <extract_dir> <output_dir> [--chapters 001,002,003]
"""

import argparse
import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

from review_rules_loader import ReviewRulesLoader, get_rules_loader
from review_by_llm import EIA_LLMReview
from chunker import TextChunker, create_chunks


# 并发配置
DEFAULT_CONCURRENT = 3
DEFAULT_TIMEOUT = 120


def review_single_chapter(
    chapter_num: str,
    chapter_dir: Path,
    rules_loader: ReviewRulesLoader,
    llm_reviewer: EIA_LLMReview,
    chunker: TextChunker,
    tables_data: List[Dict] = None,
    previous_context: str = ""
) -> Dict:
    """
    审查单个章节

    Args:
        chapter_num: 章节编号
        chapter_dir: 章节文件所在目录
        rules_loader: 规则加载器
        llm_reviewer: LLM审查器
        chunker: 分块器
        tables_data: 表格数据
        previous_context: 前一章节末尾内容

    Returns:
        审查结果
    """
    result = {
        "chapter_num": chapter_num,
        "chapter_name": "",
        "findings": [],
        "chunks_reviewed": 0,
        "errors": [],
        "start_time": datetime.now().isoformat(),
        "end_time": None
    }

    try:
        # 读取章节内容
        chapter_file = chapter_dir / f"chapter_{chapter_num}.txt"
        if not chapter_file.exists():
            result["errors"].append(f"章节文件不存在: {chapter_file}")
            return result

        content = chapter_file.read_text(encoding='utf-8')
        if not content.strip():
            result["errors"].append("章节内容为空")
            return result

        # 获取章节名称
        first_line = content.split('\n')[0].strip() if content else ""
        chapter_name = first_line if first_line else f"chapter_{chapter_num}"

        # 获取适用规则（纯文本）
        rules_text = rules_loader.get_rules_for_chapter(chapter_num, chapter_name)

        # 匹配相关表格（只调一次，返回完整文本+oversized信息）
        tables_result = _find_relevant_tables(tables_data, chapter_num, chapter_name) if tables_data else {
            "text": "", "oversized_table_ids": [], "total_table_count": 0,
            "same_chapter_count": 0, "cross_ref_count": 0
        }
        tables_text = tables_result["text"]

        result["chapter_name"] = chapter_name
        result["rules_text"] = rules_text
        result["tables"] = tables_text
        result["oversized_table_ids"] = tables_result["oversized_table_ids"]
        result["table_count"] = tables_result["total_table_count"]

        # 检查是否需要分块
        if len(content) <= 8000:
            # 不需要分块，直接审查
            findings = _review_content(
                chapter_num, chapter_name, content, rules_text,
                tables_text, previous_context, llm_reviewer
            )
            result["findings"] = findings
            result["chunks_reviewed"] = 1
        else:
            # 需要分块
            chunks = create_chunks(content, max_size=8000, overlap_lines=3)
            all_findings = []
            last_context = previous_context

            for i, chunk in enumerate(chunks):
                chunk_context = last_context if i > 0 else previous_context
                findings = _review_content(
                    chapter_num, chapter_name, chunk.content, rules_text,
                    tables_text, chunk_context, llm_reviewer
                )
                all_findings.extend(findings)
                last_context = chunk.content[-500:] if len(chunk.content) > 500 else chunk.content
                result["chunks_reviewed"] = i + 1

            # 去重
            result["findings"] = _deduplicate_findings(all_findings)

    except Exception as e:
        result["errors"].append(str(e))

    result["end_time"] = datetime.now().isoformat()
    return result


def _review_content(
    chapter_num: str,
    chapter_name: str,
    content: str,
    rules_text: str,
    tables_text: str,
    context: str,
    llm_reviewer: EIA_LLMReview
) -> List[Dict]:
    """审查一块内容

    Args:
        tables_text: 已格式化好的完整表格文本（由 process_chapter 调一次传入，
                     不在此函数内重复调 _find_relevant_tables）
    """
    try:
        review_result = llm_reviewer.review_chapter(
            chapter_num=chapter_num,
            chapter_name=chapter_name,
            chapter_content=content,
            rules_text=rules_text,
            tables=tables_text,
            context=context
        )
        return review_result.get("findings", [])
    except Exception as e:
        return [{
            "id": f"ERROR_{chapter_num}",
            "title": "审查执行错误",
            "severity": "medium",
            "confidence": "high",
            "location": chapter_name,
            "description": f"LLM审查执行失败: {str(e)}",
            "rule_id": "",
            "rule_name": "",
            "suggestion": "请重试或人工审查"
        }]


def _format_single_table(table: Dict) -> str:
    """格式化单个表格为文本（完整内容，不限行数）
    
    Args:
        table: 表格数据字典，包含 data, table_id, chapter_num
    """
    rows = table.get("data", [])
    if not rows:
        return ""
    
    table_id = table.get('table_id', '?')
    chapter_num = table.get('chapter_num', '?')
    result_lines = [f"表格 {table_id} (ch{chapter_num}):"]
    
    for row in rows:
        result_lines.append(" | ".join(str(c) for c in row))
    
    return '\n'.join(result_lines)


def _find_relevant_tables(tables_data: List[Dict], chapter_num: str, chapter_name: str = "") -> Dict[str, Any]:
    """查找与指定章节相关的表格

    匹配策略：
    1. 同 chapter_num 的表格**全部**传入（不受数量限制，完整内容）
    
    Returns:
        dict: {
            "text": 格式化后的表格文本（完整内容）,
            "oversized_table_ids": oversized表格ID列表（用于记录到DB）,
            "total_table_count": 总表格数,
        }
    """
    if not tables_data:
        return {"text": "", "oversized_table_ids": [], "total_table_count": 0}

    # 同章节表格（核心，必须全部传入）
    same_chapter_tables = [t for t in tables_data if t.get('chapter_num') == chapter_num]

    # 统计 oversized（超过20行的表格）
    oversized_table_ids = [
        t['table_id'] for t in same_chapter_tables
        if len(t.get('data', [])) > 20
    ]

    # 格式化输出（完整内容，无行数限制）
    result_lines = []
    if same_chapter_tables:
        result_lines.append(f"【本章表格（共 {len(same_chapter_tables)} 个）】")
        for t in same_chapter_tables:
            result_lines.append(_format_single_table(t))
    else:
        return {"text": "（无相关表格数据）", "oversized_table_ids": [], "total_table_count": 0}

    text = '\n'.join(result_lines)

    return {
        "text": text,
        "oversized_table_ids": oversized_table_ids,
        "total_table_count": len(same_chapter_tables),
    }


def _deduplicate_findings(findings: List[Dict]) -> List[Dict]:
    """去重findings"""
    seen = set()
    unique = []

    for finding in findings:
        # 用规则ID+描述前50字符作为key
        key = f"{finding.get('rule_id', '')}_{finding.get('description', '')[:50]}"

        if key not in seen:
            seen.add(key)
            unique.append(finding)

    return unique


async def review_chapters_async(
    extract_dir: Path,
    output_dir: Path,
    chapter_nums: Optional[List[str]] = None,
    concurrent: int = DEFAULT_CONCURRENT,
    timeout: int = DEFAULT_TIMEOUT
) -> Dict[str, Dict]:
    """
    异步并发审查多个章节

    Args:
        extract_dir: 提取目录
        output_dir: 输出目录
        chapter_nums: 要审查的章节编号列表，None表示全部
        concurrent: 并发数
        timeout: 超时时间（秒）

    Returns:
        各章节审查结果
    """
    # 初始化组件
    rules_loader = get_rules_loader()

    # 从配置文件读取 DeepSeek API Key
    import re as re_mod
    api_key = None
    cfg = Path.home() / '.hermes' / 'config.yaml'
    if cfg.exists():
        for line in cfg.read_text().split('\n'):
            if 'deepseek_api_key' in line:
                m = re_mod.search(r'deepseek_api_key:\s*([\w-]+)', line)
                if m:
                    api_key = m.group(1)
                    break
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    llm_reviewer = EIA_LLMReview(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        timeout=timeout,
        prompt_output_dir=str(output_dir / "prompts")
    )
    chunker = TextChunker()

    # 读取表格数据
    tables_data = []
    # 文件名格式：{项目名}_tables.json（如 龙岗百旺达废水_tables.json）
    tables_file = extract_dir / f"{extract_dir.parent.name.split('_')[0]}_tables.json"
    if tables_file.exists():
        tables_data = json.loads(tables_file.read_text(encoding='utf-8'))

    # 确定要审查的章节
    chapters_dir = extract_dir / "chapters"
    if not chapters_dir.exists():
        return {"error": f"章节目录不存在: {chapters_dir}"}

    if chapter_nums is None:
        # 自动发现所有章节
        chapter_nums = []
        for f in chapters_dir.glob("chapter_*.txt"):
            num = f.stem.replace("chapter_", "")
            chapter_nums.append(num)
        chapter_nums.sort()

    # 读取前一章末尾作为上下文
    previous_context = ""
    chapter_results = {}

    print(f"开始审查 {len(chapter_nums)} 个章节（{concurrent}并发）...")

    # 创建线程池执行器
    with ThreadPoolExecutor(max_workers=concurrent) as executor:
        futures = {}

        for num in chapter_nums:
            # 获取前一章末尾作为上下文
            prev_num = _get_previous_chapter(num, chapter_nums)
            if prev_num:
                prev_file = chapters_dir / f"chapter_{prev_num}.txt"
                if prev_file.exists():
                    prev_content = prev_file.read_text(encoding='utf-8')
                    previous_context = prev_content[-500:] if len(prev_content) > 500 else prev_content

            future = executor.submit(
                review_single_chapter,
                num, chapters_dir, rules_loader, llm_reviewer, chunker,
                tables_data, previous_context
            )
            futures[future] = num

        # 收集结果
        for future in as_completed(futures):
            num = futures[future]
            try:
                result = future.result()
                chapter_results[num] = result

                # 保存中间结果
                save_chapter_result(output_dir / "findings", num, result)

                findings_count = len(result.get("findings", []))
                errors_count = len(result.get("errors", []))
                print(f"  章节 {num}: {findings_count} 个发现", end="")
                if errors_count > 0:
                    print(f", {errors_count} 个错误")
                else:
                    print()
            except Exception as e:
                print(f"  章节 {num}: 审查失败 - {str(e)}")
                chapter_results[num] = {"error": str(e)}

    return chapter_results


def _get_previous_chapter(current: str, all_chapters: List[str]) -> Optional[str]:
    """获取前一章编号"""
    try:
        idx = all_chapters.index(current)
        if idx > 0:
            return all_chapters[idx - 1]
    except ValueError:
        pass
    return None


def save_chapter_result(findings_dir: Path, chapter_num: str, result: Dict):
    """保存章节审查结果"""
    findings_dir.mkdir(parents=True, exist_ok=True)

    # 查找章节名称
    chapter_name = result.get("chapter_name", f"chapter_{chapter_num}")
    safe_name = chapter_name.replace(" ", "_").replace("/", "_")[:30]

    output_file = findings_dir / f"findings_{chapter_num}_{safe_name}.json"
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='逐章审查引擎')
    parser.add_argument('extract_dir', help='extract输出目录')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('--chapters', '-c', help='要审查的章节，逗号分隔，如: 001,002,003')
    parser.add_argument('--concurrent', '-n', type=int, default=DEFAULT_CONCURRENT, help=f'并发数（默认{DEFAULT_CONCURRENT}）')
    parser.add_argument('--timeout', '-t', type=int, default=DEFAULT_TIMEOUT, help=f'LLM超时秒数（默认{DEFAULT_TIMEOUT}）')

    args = parser.parse_args()

    extract_dir = Path(args.extract_dir)
    output_dir = Path(args.output_dir)

    if not extract_dir.exists():
        print(f"错误: 目录不存在: {extract_dir}", file=sys.stderr)
        sys.exit(1)

    # 解析章节列表
    chapter_nums = None
    if args.chapters:
        chapter_nums = [c.strip() for c in args.chapters.split(',')]

    # 执行审查
    print(f"提取目录: {extract_dir}")
    print(f"输出目录: {output_dir}")
    print(f"并发数: {args.concurrent}")
    print(f"超时: {args.timeout}秒")
    print()

    results = asyncio.run(review_chapters_async(
        extract_dir, output_dir, chapter_nums,
        args.concurrent, args.timeout
    ))

    # 汇总结果
    total_findings = 0
    high_count = 0
    medium_count = 0
    low_count = 0

    for num, result in results.items():
        if "error" in result:
            continue
        for finding in result.get("findings", []):
            total_findings += 1
            severity = finding.get("severity", "low")
            if severity == "high":
                high_count += 1
            elif severity == "medium":
                medium_count += 1
            else:
                low_count += 1

    print()
    print("=" * 50)
    print("审查完成！")
    print(f"总计: {total_findings} 个发现")
    print(f"  🔴 重大: {high_count}")
    print(f"  🟡 较大: {medium_count}")
    print(f"  ⚪ 一般: {low_count}")
    print(f"结果目录: {output_dir / 'findings'}")


if __name__ == "__main__":
    main()
