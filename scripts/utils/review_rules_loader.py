#!/usr/bin/env python3
"""
环评报告书审核规则加载器

从规则库 Markdown 原文按「适用章节」字段匹配规则，
用章节真实标题做语义子串匹配，不过依赖硬编码关键词。
规则文件：reference/审核规则库.md
"""

import re
from pathlib import Path


# 规则库文件路径（主规则 + 细则补充）
RULES_DIR = Path(__file__).parent.parent.parent / "reference"
RULES_FILE = RULES_DIR / "审核规则库.md"
RULES_DETAIL_FILE = RULES_DIR / "审核规则库-细则补充.md"

# 缓存已加载的规则文本
_rules_text_cache = None


def _load_rules_text() -> str:
    """加载规则库原文（主规则 + 细则补充，带缓存）"""
    global _rules_text_cache
    if _rules_text_cache is None:
        parts = []
        # 主规则
        if not RULES_FILE.exists():
            raise FileNotFoundError(f"规则库文件不存在: {RULES_FILE}")
        parts.append(RULES_FILE.read_text(encoding="utf-8"))
        # 细则补充（可选）
        if RULES_DETAIL_FILE.exists():
            parts.append(RULES_DETAIL_FILE.read_text(encoding="utf-8"))
        _rules_text_cache = "\n\n---\n\n".join(parts)
    return _rules_text_cache


def _split_rule_blocks(text: str) -> list:
    """把 Markdown 切成独立的规则块（按 ### 标题分割）"""
    lines = text.split('\n')
    start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('##'):
            start_idx = i
            break
    content = '\n'.join(lines[start_idx:])

    blocks = re.split(r'\n(?=### )', content)
    return [b.strip() for b in blocks if b.strip()]


def _normalize_chapter_title(title: str) -> str:
    """去除'第X章'前缀，保留实质性名称，如'第一章 总则'→'总则'"""
    m = re.match(r'^第([一二三四五六七八九十百千万零]+)章\s*', title)
    if m:
        title = title[m.end():]
    return title.strip()


def _parse_applicable_chapters(block: str) -> list:
    """从规则块中解析适用章节列表"""
    for line in block.split('\n'):
        if '适用章节' in line and line.strip().startswith('- **适用章节'):
            idx = line.find('适用章节')
            rest = line[idx + 4:].lstrip('：:').strip()
            rest = rest.replace('**', '')
            chapters = [c.strip() for c in rest.split(',')]
            return [c for c in chapters if c]
    return []  # 空=通用规则


def _fuzzy_match(a: str, b: str) -> bool:
    """简单模糊匹配：检查两者是否显著重叠，阈值0.6"""
    if not a or not b:
        return False
    # 互为子串
    if a in b or b in a:
        return True
    # 单字集合重叠率
    skip_set = set('，。、；：（）()《》、')
    a_set = set(a) - skip_set
    b_set = set(b) - skip_set
    if not a_set or not b_set:
        return False
    overlap = len(a_set & b_set) / max(len(a_set), len(b_set))
    return overlap > 0.6


def _chapter_matches_applicable(chapter_name: str, applicable: list) -> bool:
    """判断章节是否匹配规则的适用章节（语义子串匹配）"""
    if not applicable:
        return True  # 通用规则
    normalized = _normalize_chapter_title(chapter_name)
    for ap in applicable:
        # 子串匹配（双向）
        if ap in normalized or normalized in ap:
            return True
        # 模糊匹配
        if _fuzzy_match(ap, normalized):
            return True
    return False


def get_rules_text_for_chapter(chapter_num: str, chapter_name: str) -> str:
    """
    获取指定章节适用的规则原文（Markdown 文本片段）。

    逻辑：
    1. 加载规则库全文
    2. 按 ### 切成独立规则块
    3. 用章节真实标题匹配规则的「适用章节」字段（语义子串匹配）
    4. 返回命中的规则块文本（无结构，纯文本）
    """
    import os, re as _re
    text = _load_rules_text()
    blocks = _split_rule_blocks(text)
    matched = []
    debug_info = []

    for block in blocks:
        if '**匹配关键词**' in block:
            continue
        applicable = _parse_applicable_chapters(block)
        if _chapter_matches_applicable(chapter_name, applicable):
            matched.append(block)
            # 提取规则ID（仅第一个）
            rule_ids = _re.findall(r'### ([A-C]-\d+)', block)
            debug_info.append(f"{rule_ids[0] if rule_ids else '?'}: {applicable}")

    # 调试日志：每次规则匹配时输出到 stderr（可从后端日志看到）
    debug_msg = (
        f"[RULES_DEBUG] chapter_num={chapter_num}, chapter_name={chapter_name!r}, "
        f"total_blocks={len(blocks)}, matched={len(matched)}, rules=[{', '.join(debug_info[:10])}]"
    )
    os.write(2, (debug_msg + '\n').encode('utf-8'))

    return '\n---\n'.join(matched)


def get_rules_loader():
    """兼容旧接口，返回规则加载器"""
    return RulesTextLoader()


class RulesTextLoader:
    """简化版加载器：只提供文本，不解析结构"""

    def get_rules_for_chapter(self, chapter_num: str, chapter_name: str = "") -> str:
        """返回适用规则的 Markdown 原文"""
        return get_rules_text_for_chapter(chapter_num, chapter_name)

    def get_all_rules(self) -> str:
        """返回规则库全文"""
        return _load_rules_text()


# 兼容旧接口别名
ReviewRulesLoader = RulesTextLoader

# 单例（兼容旧代码）
_rules_loader = None


def _get_rules_loader() -> RulesTextLoader:
    global _rules_loader
    if _rules_loader is None:
        _rules_loader = RulesTextLoader()
    return _rules_loader


if __name__ == "__main__":
    loader = RulesTextLoader()

    # 测试用例：验证章节→规则匹配
    test_cases = [
        ("概述", "B-001", "选址布局"),
        ("第一章 总则", "B-006", "评价因子"),
        ("第三章 工程分析", "B-005", "基础资料"),
        ("第六章 环保措施", "B-003", "防治措施"),
        ("第八章 环境风险评价", "C-017", "风险评价"),
    ]

    for ch_name, expected_rule_prefix, keyword in test_cases:
        text = loader.get_rules_for_chapter("000", ch_name)
        blocks = [b for b in text.split('---') if b.strip()]
        matched_ids = re.findall(r'### (B-\d+|C-\d+|A-\d+)', text)
        print(f"\n章节「{ch_name}」→ {len(blocks)} 条规则: {matched_ids[:5]}")
