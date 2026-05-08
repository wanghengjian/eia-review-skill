#!/usr/bin/env python3
"""
规则关键词检索审核引擎

架构：
  load_rules()         → 解析规则文件，返回规则列表（含 search_keywords）
  keyword_search()     → 全文关键词检索，返回匹配位置列表
  extract_context()    → 提取匹配点附近上下文（±window字）
  review_rule()        → 单规则 LLM 审核
  run_keyword_review() → 主入口：规则驱动逐条审核
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# ─── 1. 规则解析 ─────────────────────────────────────────────────────────────

def load_rules(rules_file: Path) -> List[Dict[str, Any]]:
    """解析 Markdown 规则文件，返回结构化规则列表。

    支持的字段（每条规则）：
      - id           : 规则ID（如 B-001）
      - title        : 规则标题
      - category     : B/C/A
      - situation    : 情形描述
      - chapters     : 适用章节（逗号分隔）
      - keywords     : 检索关键词（逗号分隔）
      - references   : 参考文件
      - check_steps  : 审核步骤（每行一个步骤）
      - severity     : high/medium/low
    """
    text = rules_file.read_text(encoding='utf-8')

    # 按 ### 分割规则块（第一个块是文档头，跳过）
    raw_blocks = re.split(r'\n(?=### [BCA]-\d+)', text)
    rules = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue

        # 解析规则ID和标题（第一行：### B-001 标题）
        header_match = re.match(r'^### ([BCA]-\d+)\s+(.+)', block)
        if not header_match:
            continue
        rule_id = header_match.group(1).strip()
        rule_title = header_match.group(2).strip()

        # 解析 category
        if rule_id.startswith('B'):
            category = 'B'
            severity = 'high'
        elif rule_id.startswith('C'):
            category = 'C'
            severity = 'medium'
        else:
            category = 'A'
            severity = 'low'

        # 提取 keywords（逗号/顿号分隔）
        kw_match = re.search(r'\*\*keywords\*\*[:：]\s*(.+?)(?:\n-|\n###|\n##|$)', block, re.DOTALL)
        if kw_match:
            keywords = [k.strip() for k in re.split(r'[,，、、]', kw_match.group(1)) if k.strip()]
        else:
            keywords = []

        # 提取 situation（注意分隔符是 **情形**： 冒号在 ** 之后）
        situation_match = re.search(r'\*\*情形\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|$)', block, re.DOTALL)
        situation = situation_match.group(1).strip() if situation_match else ''

        # 提取适用章节
        chapter_match = re.search(r'\*\*适用章节\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|$)', block, re.DOTALL)
        chapters = chapter_match.group(1).strip() if chapter_match else ''

        # 提取参考文件
        ref_match = re.search(r'\*\*参考文件\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|\*\*审核步骤|$)', block, re.DOTALL)
        references = ref_match.group(1).strip() if ref_match else ''

        # 提取审核步骤
        steps_match = re.search(r'\*\*审核步骤\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|$)', block, re.DOTALL)
        check_steps = []
        if steps_match:
            steps_text = steps_match.group(1).strip()
            # 提取编号步骤（1. 2. 或 1、2、）
            steps = re.findall(r'(?:^\d+[.、]|\n\s*-?\s*\d+[.、])\s*(.+)', steps_text, re.MULTILINE)
            if steps:
                check_steps = [s.strip() for s in steps]
            else:
                # 非编号段落，整个作为步骤列表
                check_steps = [l.strip() for l in steps_text.split('\n') if l.strip() and len(l.strip()) > 5]

        rules.append({
            'id': rule_id,
            'title': rule_title,
            'category': category,
            'severity': severity,
            'situation': situation,
            'chapters': chapters,
            'keywords': keywords,
            'references': references,
            'check_steps': check_steps,
        })

    return rules


# ─── 2. 关键词检索 ─────────────────────────────────────────────────────────

def keyword_search(keywords: List[str], text: str) -> List[Tuple[int, str]]:
    """全文检索关键词，返回 [(匹配位置, 关键词)] 列表。

    使用重叠匹配：同一个位置匹配到多个关键词只返回一个（第一个命中的）。
    返回列表按位置排序。
    """
    positions = []
    for kw in keywords:
        if not kw or len(kw) < 2:
            continue
        # 找所有匹配位置（不区分大小写）
        for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
            positions.append((m.start(), kw))
    # 按位置排序，去重（同一位置保留第一个关键词）
    positions.sort(key=lambda x: x[0])
    seen_pos = set()
    unique = []
    for pos, kw in positions:
        if pos not in seen_pos:
            seen_pos.add(pos)
            unique.append((pos, kw))
    return unique


def extract_contexts(
    matches: List[Tuple[int, str]],
    text: str,
    window: int = 800
) -> List[Dict[str, Any]]:
    """从匹配点提取上下文段落。

    Args:
        matches    : keyword_search() 返回的匹配列表
        text       : 全文
        window     : 匹配点前后各取多少字符（默认800）

    Returns:
        上下文段落列表，每项含：
          - keyword_matched : 命中的关键词
          - context         : 周围文本（去空白）
          - position        : 在全文中的位置
    """
    contexts = []
    for pos, kw in matches:
        start = max(0, pos - window)
        end = min(len(text), pos + window)
        raw = text[start:end]
        # 清理多余空白
        cleaned = re.sub(r'[ \t]+', ' ', raw)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        contexts.append({
            'keyword_matched': kw,
            'context': cleaned.strip(),
            'position': pos,
        })
    return contexts


# ─── 3. LLM 审核 ────────────────────────────────────────────────────────────

def review_rule_with_llm(
    rule: Dict[str, Any],
    contexts: List[Dict[str, Any]],
    project_info: Dict[str, str],
    api_key: str,
    model: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com",
) -> Optional[Dict[str, Any]]:
    """对单条规则进行 LLM 审核。

    Returns:
        None 如果无相关内容和无问题；否则返回 finding dict。
    """
    if not contexts:
        return None

    # 构造上下文摘要（最多取前5个匹配，避免 token 爆炸）
    context_snippets = []
    for i, ctx in enumerate(contexts[:5], 1):
        snippet = ctx['context']
        if len(snippet) > 600:
            snippet = snippet[:600] + '...'
        context_snippets.append(
            f"【相关段落{i}」（关键词「{ctx['keyword_matched']}」）\n{snippet}"
        )
    combined_context = '\n\n---\n\n'.join(context_snippets)

    prompt = f"""## 角色
你是一名资深的深圳市环境影响评价技术专家，负责对环评报告书进行技术审核。

## 待审核项目
- 项目名称：{project_info.get('project_name', '未知')}
- 建设单位：{project_info.get('company', '未知')}
- 环评类别：{project_info.get('evaluation_level', '未知')}

## 审核规则
**规则ID**：{rule['id']}
**规则标题**：{rule['title']}
**情形**（违反此规则的情形）：
{rule['situation']}

**适用章节**：{rule['chapters']}
**参考文件**：{rule['references']}

**审核要点**：
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(rule['check_steps'][:5])) if rule['check_steps'] else '（无具体步骤，按情形描述判断）'}

## 报告中的相关段落
{combined_context}

## 审核要求
请仔细阅读上述「情形」描述和报告相关段落，判断报告是否存在该规则所描述的问题。

输出格式（严格按以下JSON格式，不要有其他内容）：
{{
  "has_problem": true或false,
  "description": "如果发现问题，用一段话简明描述问题核心（不超过200字）",
  "legal_basis": "违反的具体条款或标准名称（如有）",
  "suggestion": "修改建议，不超过100字",
  "confidence": "high/medium/low 表示你对判断的确信程度"
}}

注意：
- 只有在报告内容与「情形」描述高度吻合时才判定为 has_problem=true
- description 应具体指出报告中的具体表述问题，不要泛泛而谈
- confidence 高 = 报告中有明确文字证据；中 = 有相关表述但不够确凿；低 = 仅凭现有段落难以确定
"""

    import urllib.request
    import urllib.error

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result["choices"][0]["message"]["content"].strip()

        # 提取 JSON
        json_match = re.search(r'\{[\s\S]+\}', content)
        if not json_match:
            return None
        llm_result = json.loads(json_match.group())

        if not llm_result.get('has_problem'):
            return None

        return {
            "rule_id": rule['id'],
            "rule_title": rule['title'],
            "category": rule['category'],
            "severity": rule['severity'],
            "situation": rule['situation'],
            "keyword_matched": ', '.join(set(ctx['keyword_matched'] for ctx in contexts[:5])),
            "relevant_content": combined_context[:500],
            "description": llm_result.get('description', ''),
            "legal_basis": llm_result.get('legal_basis', ''),
            "suggestion": llm_result.get('suggestion', ''),
            "confidence": llm_result.get('confidence', 'medium'),
            "source_chapter": "全文检索",
            "source_chapter_name": "全文检索",
        }

    except Exception as e:
        return {
            "rule_id": rule['id'],
            "rule_title": rule['title'],
            "category": rule['category'],
            "severity": rule['severity'],
            "keyword_matched": ', '.join(set(ctx['keyword_matched'] for ctx in contexts[:5])),
            "description": f"[LLM调用失败: {str(e)}]",
            "legal_basis": "",
            "suggestion": "请人工审核",
            "confidence": "low",
            "source_chapter": "全文检索",
            "source_chapter_name": "全文检索",
        }


# ─── 4. 主入口 ─────────────────────────────────────────────────────────────

def run_keyword_review(
    full_text: str,
    project_info: Dict[str, str],
    rules_file: Path,
    output_dir: Path,
    api_key: str,
    model: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com",
    concurrent: int = 3,
    min_keyword_hits: int = 1,
) -> Tuple[List[Dict], Dict]:
    """规则关键词检索审核主入口。

    Args:
        full_text        : 报告全文
        project_info      : 项目基本信息
        rules_file        : 规则文件路径
        output_dir        : 输出目录
        api_key           : DeepSeek API Key
        model             : 模型名
        base_url          : API 地址
        concurrent        : 并发 LLM 调用数
        min_keyword_hits  : 至少命中几个关键词才调用 LLM

    Returns:
        (findings列表, summary字典)
    """
    # 1. 加载规则
    rules = load_rules(rules_file)
    print(f"[关键词引擎] 加载规则 {len(rules)} 条")

    # 2. 逐规则检索 + LLM 审核
    all_findings = []
    stats = {
        "total_rules": len(rules),
        "rules_with_hits": 0,
        "rules_with_findings": 0,
        "skipped_no_keywords": 0,
    }

    for rule in rules:
        kw_list = rule['keywords']
        if not kw_list:
            stats["skipped_no_keywords"] += 1
            continue

        # 关键词检索
        matches = keyword_search(kw_list, full_text)
        if len(matches) < min_keyword_hits:
            continue

        stats["rules_with_hits"] += 1

        # 提取上下文
        contexts = extract_contexts(matches, full_text)

        # LLM 审核
        finding = review_rule_with_llm(
            rule=rule,
            contexts=contexts,
            project_info=project_info,
            api_key=api_key,
            model=model,
            base_url=base_url,
        )

        if finding:
            all_findings.append(finding)
            stats["rules_with_findings"] += 1
            print(f"  🔍 {rule['id']} {rule['title'][:20]} → 发现问题")

    # 3. 按严重程度分级
    classified = {"high": [], "medium": [], "low": []}
    for f in all_findings:
        sev = f.get("severity", "low")
        if sev == "high":
            classified["high"].append(f)
        elif sev == "medium":
            classified["medium"].append(f)
        else:
            classified["low"].append(f)

    summary = {
        "total_rules": stats["total_rules"],
        "rules_with_hits": stats["rules_with_hits"],
        "rules_with_findings": stats["rules_with_findings"],
        "skipped_no_keywords": stats["skipped_no_keywords"],
        "total_findings": len(all_findings),
        "high_count": len(classified["high"]),
        "medium_count": len(classified["medium"]),
        "low_count": len(classified["low"]),
        "review_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 4. 保存 findings
    output_dir.mkdir(parents=True, exist_ok=True)
    findings_file = output_dir / "keyword_findings.json"
    findings_file.write_text(
        json.dumps({
            "findings": all_findings,
            "classified": classified,
            "summary": summary,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n[关键词引擎] 完成：{len(all_findings)} 条发现（重大 {summary['high_count']} / 较大 {summary['medium_count']} / 一般 {summary['low_count']}）")
    print(f"[关键词引擎] 结果保存至：{findings_file}")

    return all_findings, classified, summary


# ─── 5. CLI ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='规则关键词检索审核引擎')
    parser.add_argument('text_file', help='报告全文文本文件')
    parser.add_argument('project_info', help='项目信息JSON文件')
    parser.add_argument('rules_file', help='规则文件（Markdown）')
    parser.add_argument('output_dir', help='输出目录')
    args = parser.parse_args()

    full_text = Path(args.text_file).read_text(encoding='utf-8')
    project_info = json.loads(Path(args.project_info).read_text(encoding='utf-8'))
    rules_file = Path(args.rules_file)

    # 读取 API Key
    config_file = Path.home() / '.hermes' / 'config.yaml'
    import yaml
    config = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
    api_key = config.get('deepseek_api_key', '')
    if not api_key:
        print("错误：未配置 deepseek_api_key")
        return

    findings, classified, summary = run_keyword_review(
        full_text=full_text,
        project_info=project_info,
        rules_file=rules_file,
        output_dir=Path(args.output_dir),
        api_key=api_key,
    )

    print(f"\n结果：{summary['total_findings']} 条发现")


if __name__ == '__main__':
    main()
