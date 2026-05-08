#!/usr/bin/env python3
"""
环评报告书快审 - 完整流程统一入口

功能：
- 执行完整快审流程（步骤1-7）
- 支持分步执行和状态查看
- 支持中断恢复

用法：
    # 完整流程
    python3 run_full_review.py -n "项目名称" -d "报告书.docx" --all

    # 查看状态
    python3 run_full_review.py -n "项目名称" -d "报告书.docx" --status

    # 仅提取
    python3 run_full_review.py -n "项目名称" -d "报告书.docx" --extract

    # 仅审核
    python3 run_full_review.py -n "项目名称" -d "报告书.docx" --review

    # 仅生成报告
    python3 run_full_review.py -n "项目名称" -d "报告书.docx" --report
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


# 脚本目录
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent.parent

# 添加utils到路径
sys.path.insert(0, str(SCRIPT_DIR / "utils"))

# 导入各步骤脚本
from extract_chapters_textutil import extract_from_docx, extract_from_pdf, splitChapters, save_output, convert_doc_to_docx
from chapter_completeness import check_chapter_completeness, generate_completeness_report
from process_chapters_v2 import review_chapters_async
from generate_unified_report import deduplicate_and_classify, generate_report


# 默认输出根目录（工作目录，不放在 skill 本体里）
WORKSPACE_ROOT = Path.home() / ".hermes" / "workspace" / "eia-review"
DEFAULT_OUTPUT_ROOT = WORKSPACE_ROOT / "output"


class ReviewSession:
    """审核会话管理"""

    def __init__(self, project_name: str, doc_path: str, session_name: str = "review_001"):
        self.project_name = project_name
        self.doc_path = Path(doc_path)
        self.session_name = session_name

        # 创建会话目录（如果已存在已完成会话，自动递增编号）
        safe_project = "".join(c if c.isalnum() or c in ('_', '-', ' ') else '_' for c in project_name)[:50]
        self.session_dir = DEFAULT_OUTPUT_ROOT / f"{safe_project}_{session_name}"

        # 检查目录是否已存在且有已完成步骤 → 自动找下一个可用编号
        if self.session_dir.exists():
            existing_status = self.session_dir / "status.json"
            if existing_status.exists():
                try:
                    status_data = json.loads(existing_status.read_text(encoding='utf-8'))
                    if status_data.get("steps_completed"):
                        # 已有完成步骤 → 递增找新编号
                        base_name = f"{safe_project}_review_"
                        parent = DEFAULT_OUTPUT_ROOT
                        existing = sorted([d.name for d in parent.iterdir() if d.name.startswith(base_name)])
                        # 解析已有编号，找最大
                        max_num = 0
                        for dname in existing:
                            suffix = dname[len(base_name):]
                            m = re.match(r'^(\d+)$', suffix)
                            if m:
                                max_num = max(max_num, int(m.group(1)))
                        new_num = max_num + 1
                        self.session_name = f"review_{new_num:03d}"
                        self.session_dir = DEFAULT_OUTPUT_ROOT / f"{safe_project}_{self.session_name}"
                except Exception:
                    pass

        self.session_dir.mkdir(parents=True, exist_ok=True)

        # 各步骤目录
        self.extract_dir = self.session_dir / "extract"
        self.findings_dir = self.session_dir / "findings"

        # 状态文件
        self.status_file = self.session_dir / "status.json"

        # 加载状态
        self.status = self._load_status()

    def _load_status(self) -> Dict:
        """加载状态"""
        if self.status_file.exists():
            return json.loads(self.status_file.read_text(encoding='utf-8'))
        return {
            "project_name": self.project_name,
            "doc_path": str(self.doc_path),
            "session_name": self.session_name,
            "created_at": datetime.now().isoformat(),
            "steps_completed": [],
            "last_updated": None
        }

    def save_status(self):
        """保存状态"""
        self.status["last_updated"] = datetime.now().isoformat()
        self.status_file.write_text(json.dumps(self.status, ensure_ascii=False, indent=2), encoding='utf-8')

    def is_step_completed(self, step: str) -> bool:
        """检查步骤是否完成"""
        return step in self.status.get("steps_completed", [])

    def mark_step_completed(self, step: str):
        """标记步骤完成"""
        if step not in self.status["steps_completed"]:
            self.status["steps_completed"].append(step)
        self.save_status()

    def reset(self):
        """重置状态"""
        self.status["steps_completed"] = []
        self.save_status()


def step1_extract(session: ReviewSession) -> bool:
    """步骤1：内容提取"""
    print("\n" + "=" * 60)
    print("步骤1：内容提取")
    print("=" * 60)

    if session.is_step_completed("extract"):
        print("（已执行，跳过）")
        return True

    try:
        doc_path = session.doc_path
        suffix = doc_path.suffix.lower()

        print(f"输入文件: {doc_path}")

        if suffix == '.pdf':
            print("提取PDF文件...")
            full_text, tables, project_info = extract_from_pdf(str(doc_path))
        elif suffix in ['.docx', '.doc']:
            if suffix == '.doc':
                print("转换DOC为DOCX...")
                docx_path = convert_doc_to_docx(str(doc_path))
            else:
                docx_path = str(doc_path)

            print("提取DOCX文件...")
            full_text, tables, project_info = extract_from_docx(docx_path)
        else:
            print(f"错误: 不支持的文件格式: {suffix}")
            return False

        print(f"提取完成: {len(full_text)} 字符, {len(tables)} 个表格")

        # 分割章节
        print("分割章节...")
        chapters, toc_content = splitChapters(full_text)
        print(f"分割完成: {len(chapters)} 个章节")

        # 保存输出
        print("保存文件...")
        session.extract_dir.mkdir(parents=True, exist_ok=True)
        prefix = re.sub(r'[^\w\u4e00-\u9fff-]', '_', session.project_name)[:50]

        (session.extract_dir / f"{prefix}_full_text.txt").write_text(full_text, encoding='utf-8')
        (session.extract_dir / f"{prefix}_tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding='utf-8')
        if toc_content:
            (session.extract_dir / "toc.txt").write_text(toc_content, encoding='utf-8')
            print(f"目录已保存: {len(toc_content)} 字符")
        (session.extract_dir / "项目信息.json").write_text(json.dumps(project_info, ensure_ascii=False, indent=2), encoding='utf-8')

        chapters_dir = session.extract_dir / "chapters"
        chapters_dir.mkdir(exist_ok=True)
        for num, data in sorted(chapters.items()):
            (chapters_dir / f"chapter_{num}.txt").write_text(data["content"], encoding='utf-8')

        session.mark_step_completed("extract")
        print("✅ 步骤1完成")
        return True

    except Exception as e:
        print(f"❌ 步骤1失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def step2_completeness(session: ReviewSession) -> bool:
    """步骤2：章节完整性审查"""
    print("\n" + "=" * 60)
    print("步骤2：章节完整性审查")
    print("=" * 60)

    if session.is_step_completed("completeness"):
        print("（已执行，跳过）")
        return True

    try:
        chapters_dir = session.extract_dir / "chapters"
        if not chapters_dir.exists():
            print("错误: 章节目录不存在，请先执行步骤1")
            return False

        # 加载项目信息
        info_file = session.extract_dir / "项目信息.json"
        project_info = {}
        if info_file.exists():
            project_info = json.loads(info_file.read_text(encoding='utf-8'))

        # 检查完整性
        print("检查章节完整性...")
        results = check_chapter_completeness(chapters_dir)

        # 保存结果
        findings_dir = session.findings_dir / "章节完整性"
        findings_dir.mkdir(parents=True, exist_ok=True)

        (findings_dir / "check_result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')

        report = generate_completeness_report(results, project_info)
        (findings_dir / "完整性报告.md").write_text(report, encoding='utf-8')

        status_icon = "✅" if results["status"] == "complete" else "⚠️"
        print(f"{status_icon} 章节完整性: {results['status']}")
        print(f"  发现章节: {len(results['chapters_found'])}")
        if results.get("missing_chapters"):
            print(f"  缺失章节: {', '.join(results['missing_chapters'])}")
        if results.get("empty_chapters"):
            print(f"  空章节: {', '.join(results['empty_chapters'])}")

        session.mark_step_completed("completeness")
        print("✅ 步骤2完成")
        return True

    except Exception as e:
        print(f"❌ 步骤2失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def step3_review(session: ReviewSession, concurrent: int = 3, timeout: int = 120) -> bool:
    """步骤3-5：章节审查（并发）"""
    print("\n" + "=" * 60)
    print("步骤3-5：章节审查（并发）")
    print("=" * 60)

    if session.is_step_completed("review"):
        print("（已执行，跳过）")
        return True

    try:
        # 检查前置步骤
        if not session.extract_dir.exists():
            print("错误: 提取目录不存在，请先执行步骤1")
            return False

        chapters_dir = session.extract_dir / "chapters"
        if not chapters_dir.exists():
            print("错误: 章节目录不存在")
            return False

        # 发现要审查的章节
        chapter_nums = []
        for f in chapters_dir.glob("chapter_*.txt"):
            num = f.stem.replace("chapter_", "")
            chapter_nums.append(num)
        chapter_nums.sort()

        print(f"发现 {len(chapter_nums)} 个章节")
        print(f"并发数: {concurrent}, 超时: {timeout}秒")

        # 执行审查
        results = await review_chapters_async(
            session.extract_dir, session.session_dir,
            chapter_nums, concurrent, timeout
        )

        # 统计
        total_findings = 0
        for num, result in results.items():
            if "error" not in result:
                total_findings += len(result.get("findings", []))

        print(f"\n审查完成: {total_findings} 个发现")

        session.mark_step_completed("review")
        print("✅ 步骤3-5完成")
        return True

    except Exception as e:
        print(f"❌ 步骤3-5失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def step6_generate(session: ReviewSession) -> bool:
    """步骤6-7：汇总生成报告"""
    print("\n" + "=" * 60)
    print("步骤6-7：汇总生成报告")
    print("=" * 60)

    if session.is_step_completed("report"):
        print("（已执行，跳过）")
        return True

    try:
        findings_dir = session.findings_dir
        if not findings_dir.exists():
            print("错误: findings目录不存在，请先执行步骤3-5")
            return False

        # 加载项目信息
        info_file = session.extract_dir / "项目信息.json"
        project_info = {}
        if info_file.exists():
            project_info = json.loads(info_file.read_text(encoding='utf-8'))

        # 加载章节完整性结果
        completeness_result = None
        completeness_file = findings_dir / "章节完整性" / "check_result.json"
        if completeness_file.exists():
            completeness_result = json.loads(completeness_file.read_text(encoding='utf-8'))

        # 加载所有findings
        print("加载审查结果...")
        all_findings = []
        summary = {"total_files": 0, "chapters_reviewed": [], "errors": [], "review_mode": "chapter"}

        for f in findings_dir.glob("findings_*.json"):
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                summary["total_files"] += 1
                summary["chapters_reviewed"].append(data.get("chapter_name", data.get("chapter_num", f.stem)))
                for finding in data.get("findings", []):
                    finding["source_chapter"] = data.get("chapter_num", "unknown")
                    finding["source_chapter_name"] = data.get("chapter_name", data.get("chapter_num", "unknown"))
                    all_findings.append(finding)
                if data.get("errors"):
                    summary["errors"].extend(data["errors"])
            except Exception as e:
                print(f"  警告: 读取 {f.name} 失败: {e}")

        print(f"共加载 {len(all_findings)} 个发现")

        # 去重分级
        print("去重分级...")
        classified = deduplicate_and_classify(all_findings)

        # 生成报告
        print("生成报告...")
        report = generate_report(project_info, classified, summary, completeness_result)

        # 保存
        output_file = session.session_dir / "审查报告_统一版.md"
        output_file.write_text(report, encoding='utf-8')

        # 打印结论
        high = len(classified["high"])
        medium = len(classified["medium"])
        low = len(classified["low"])

        print(f"\n{'=' * 50}")
        print(f"审查完成！")
        print(f"总计: {high + medium + low} 个发现")
        print(f"  🔴 重大: {high}")
        print(f"  🟡 较大: {medium}")
        print(f"  ⚪ 一般: {low}")
        print(f"\n结论: ", end="")

        if high > 0:
            print(f"❌ 不通过（存在 {high} 项重大缺陷）")
        elif medium > 0:
            print(f"⚠️ 修改（存在 {medium} 项较大缺陷）")
        else:
            print(f"✅ 通过")

        print(f"\n报告已保存: {output_file}")

        session.mark_step_completed("report")
        print("✅ 步骤6-7完成")
        return True

    except Exception as e:
        print(f"❌ 步骤6-7失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def show_status(session: ReviewSession):
    """显示状态"""
    print(f"\n{'=' * 60}")
    print(f"审核状态: {session.session_dir.name}")
    print(f"{'=' * 60}")

    print(f"\n项目: {session.project_name}")
    print(f"文件: {session.doc_path}")
    print(f"会话: {session.session_name}")
    print(f"\n已完成步骤:")
    for step in session.status.get("steps_completed", []):
        print(f"  ✅ {step}")
    if not session.status.get("steps_completed"):
        print("  （无）")

    # 显示报告
    report_file = session.session_dir / "审查报告_统一版.md"
    if report_file.exists():
        print(f"\n审查报告: {report_file}")
    else:
        print(f"\n审查报告: （未生成）")


def main():
    parser = argparse.ArgumentParser(
        description='环评报告书快审 - 完整流程',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 完整流程
  python3 run_full_review.py -n "XX项目" -d "报告书.docx" --all

  # 查看状态
  python3 run_full_review.py -n "XX项目" -d "报告书.docx" --status

  # 分步执行
  python3 run_full_review.py -n "XX项目" -d "报告书.docx" --extract
  python3 run_full_review.py -n "XX项目" -d "报告书.docx" --completeness
  python3 run_full_review.py -n "XX项目" -d "报告书.docx" --review
  python3 run_full_review.py -n "XX项目" -d "报告书.docx" --report

  # 同一项目第二次审核
  python3 run_full_review.py -n "XX项目" -d "报告书_v2.docx" --session "review_002" --all
        """
    )

    parser.add_argument('-n', '--project', required=True, help='项目名称')
    parser.add_argument('-d', '--docx', required=True, help='报告书文件路径')
    parser.add_argument('--session', default='review_001', help='会话标识（同一项目多次审核时使用）')
    parser.add_argument('--concurrent', '-c', type=int, default=3, help='并发审查章节数（默认3）')
    parser.add_argument('--timeout', '-t', type=int, default=120, help='LLM超时秒数（默认120）')

    # 流程控制
    parser.add_argument('--all', action='store_true', help='执行完整流程')
    parser.add_argument('--status', action='store_true', help='查看状态')
    parser.add_argument('--extract', action='store_true', help='仅执行步骤1（内容提取）')
    parser.add_argument('--completeness', action='store_true', help='仅执行步骤2（完整性审查）')
    parser.add_argument('--review', action='store_true', help='仅执行步骤3-5（章节审查）')
    parser.add_argument('--report', action='store_true', help='仅执行步骤6-7（生成报告）')
    parser.add_argument('--reset', action='store_true', help='重置状态，重新开始')

    args = parser.parse_args()

    # 创建会话
    session = ReviewSession(args.project, args.docx, args.session)

    if args.reset:
        print("重置状态...")
        session.reset()
        print("状态已重置")

    # 显示状态
    if args.status:
        show_status(session)
        return

    # 验证文件
    if not session.doc_path.exists():
        print(f"错误: 文件不存在: {session.doc_path}")
        sys.exit(1)

    print(f"\n项目: {session.project_name}")
    print(f"文件: {session.doc_path}")
    print(f"会话目录: {session.session_dir}")

    success = True

    if args.all:
        # 完整流程
        success = step1_extract(session)
        if success:
            success = step2_completeness(session)
        if success:
            import asyncio
            success = asyncio.run(step3_review(session, args.concurrent, args.timeout))
        if success:
            success = step6_generate(session)

    else:
        # 分步执行
        if args.extract:
            success = step1_extract(session)

        if args.completeness:
            success = step2_completeness(session)

        if args.review:
            import asyncio
            success = asyncio.run(step3_review(session, args.concurrent, args.timeout))

        if args.report:
            success = step6_generate(session)

        if not any([args.extract, args.completeness, args.review, args.report]):
            print("请指定执行选项（--all 或 --extract/--completeness/--review/--report）")
            print("使用 --help 查看帮助")
            sys.exit(1)

    if success:
        print("\n✅ 任务完成！")
        show_status(session)
    else:
        print("\n❌ 任务失败，请检查错误信息")
        sys.exit(1)


if __name__ == "__main__":
    main()
