#!/usr/bin/env python3
"""
为规则库生成 keywords 字段。
读取每条规则的 situation + check_steps + references，
让 LLM 生成 5-8 个检索关键词。
"""

import json
import re
import sys
import urllib.request
import urllib.error
import time
from pathlib import Path
import yaml


# ─── 从 keyword_review_engine 复制的规则解析器 ─────────────────────────────

def load_rules(rules_file: Path):
    text = rules_file.read_text(encoding='utf-8')
    raw_blocks = re.split(r'\n(?=### [BCA]-\d+)', text)
    rules = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        header_match = re.match(r'^### ([BCA]-\d+)\s+(.+)', block)
        if not header_match:
            continue
        rule_id = header_match.group(1).strip()
        rule_title = header_match.group(2).strip()

        if rule_id.startswith('B'):
            category, severity = 'B', 'high'
        elif rule_id.startswith('C'):
            category, severity = 'C', 'medium'
        else:
            category, severity = 'A', 'low'

        situation_match = re.search(r'\*\*情形\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|$)', block, re.DOTALL)
        situation = situation_match.group(1).strip() if situation_match else ''

        chapter_match = re.search(r'\*\*适用章节\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|$)', block, re.DOTALL)
        chapters = chapter_match.group(1).strip() if chapter_match else ''

        ref_match = re.search(r'\*\*参考文件\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|\*\*审核步骤|$)', block, re.DOTALL)
        references = ref_match.group(1).strip() if ref_match else ''

        steps_match = re.search(r'\*\*审核步骤\*\*[:：]\s*(.+?)(?=\n- \*\*|\n###|\n##|$)', block, re.DOTALL)
        check_steps = []
        if steps_match:
            steps_text = steps_match.group(1).strip()
            steps = re.findall(r'(?:^\d+[.、]|\n\s*-?\s*\d+[.、])\s*(.+)', steps_text, re.MULTILINE)
            check_steps = [s.strip() for s in steps] if steps else [l.strip() for l in steps_text.split('\n') if l.strip() and len(l.strip()) > 5]

        rules.append({
            'id': rule_id,
            'title': rule_title,
            'category': category,
            'severity': severity,
            'situation': situation,
            'chapters': chapters,
            'references': references,
            'check_steps': check_steps,
        })
    return rules


# ─── LLM 生成关键词 ────────────────────────────────────────────────────────

def generate_keywords_for_rule(rule: dict, api_key: str, model: str = "deepseek-chat") -> list:
    prompt = f"""你是一名深圳市环境影响评价领域的信息检索专家。

请为以下审核规则生成 5-8 个中文检索关键词，用于在环评报告书中全文检索相关段落。

规则信息：
- 规则ID：{rule['id']}
- 规则标题：{rule['title']}
- 违反情形：{rule['situation']}
- 审核步骤：{'；'.join(rule['check_steps']) if rule['check_steps'] else '无'}
- 参考文件：{rule['references']}

要求：
1. 每个关键词长度 2-8 个字
2. 要覆盖规则的核心概念（至少2-3个）、常见表述（2-3个）、法规标准名（1-2个）
3. 优先选报告中最可能出现的实际文字（如具体法条名、污染物名、工艺名）
4. 不要选太泛的词（如"环保"、"项目"），也不要选太专的词（如精确的数值）
5. 返回格式：只返回一个 JSON 数组，其他什么都不写

示例格式：
["产业结构调整指导目录", "淘汰类", "限制类", "选址", "三线一单", "负面清单", "不符合"]

请生成："""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 200,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result["choices"][0]["message"]["content"].strip()

        json_match = re.search(r'\[[\s\S]+\]', content)
        if json_match:
            keywords = json.loads(json_match.group())
            if isinstance(keywords, list) and all(isinstance(k, str) for k in keywords):
                return keywords
        print(f"  [WARN] {rule['id']} 返回格式异常: {content[:100]}", file=sys.stderr)
        return []

    except Exception as e:
        print(f"  [ERROR] {rule['id']} 调用失败: {e}", file=sys.stderr)
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description='为规则库生成 keywords 字段')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不写入文件')
    parser.add_argument('--model', default='deepseek-chat', help='模型名')
    args = parser.parse_args()

    config_file = Path.home() / '.hermes' / 'config.yaml'
    if not config_file.exists():
        print("错误：找不到 ~/.hermes/config.yaml")
        return
    config = yaml.safe_load(config_file.read_text())

    # 兼容不同格式：deepseek_api_key 或 providers.deepseek.api_key
    api_key = config.get('deepseek_api_key', '')
    if not api_key:
        # 尝试从 providers 嵌套里找
        providers = config.get('providers', {})
        deepseek_cfg = providers.get('deepseek', {})
        api_key = deepseek_cfg.get('api_key', '')
    if not api_key:
        # 直接从 config 全文搜索含 sk- 的行
        for line in config_file.read_text().split('\n'):
            if 'sk-' in line and 'api_key' in line:
                m = re.search(r'api_key:\s*([\w-]+)', line)
                if m:
                    api_key = m.group(1)
                    break
    if not api_key:
        print("错误：未配置 deepseek_api_key")
        return

    scripts_dir = Path(__file__).parent
    # reference 在 scripts 的上一级
    skill_dir = scripts_dir.parent
    rules_file = skill_dir / 'reference' / '审核规则库.md'
    output_file = skill_dir / 'reference' / '审核规则库_keyword_generated.md'

    rules = load_rules(rules_file)
    print(f"加载规则 {len(rules)} 条，开始生成关键词...\n")

    updated_rules = []
    for i, rule in enumerate(rules, 1):
        print(f"[{i}/{len(rules)}] {rule['id']} {rule['title'][:15]}...", end='', flush=True)
        keywords = generate_keywords_for_rule(rule, api_key, args.model)
        rule['generated_keywords'] = keywords
        updated_rules.append(rule)
        if keywords:
            print(f" ✓ {keywords}")
        else:
            print(" ✗")
        time.sleep(0.3)

    # 预览
    print("\n=== 关键词预览 ===")
    for r in updated_rules:
        kws = r.get('generated_keywords', [])
        print(f"{r['id']} {r['title'][:15]}: {kws if kws else '(无)'}")
    print(f"\n总计：{sum(1 for r in updated_rules if r.get('generated_keywords'))}/{len(updated_rules)} 条有关键词")

    if args.dry_run:
        return

    # 更新文件
    original_text = rules_file.read_text(encoding='utf-8')
    new_text = original_text

    for rule in updated_rules:
        if not rule.get('generated_keywords'):
            continue
        kw_str = '、'.join(rule['generated_keywords'])

        rule_block_match = re.search(
            rf'(### {re.escape(rule["id"])} .+?)(?=(?:### [BCA]-\d+ )|\Z)',
            new_text,
            re.DOTALL,
        )
        if not rule_block_match:
            continue

        block_content = rule_block_match.group(1)
        block_start = rule_block_match.start()
        block_end = rule_block_match.end()

        kw_line = f"- **keywords**：{kw_str}\n"
        sev_match = re.search(r'(- \*\*严重程度\*\*[^\n]*\n)', block_content)
        if sev_match:
            insert_pos = block_content.find(sev_match.group(1)) + len(sev_match.group(1))
            new_block = block_content[:insert_pos] + kw_line + block_content[insert_pos:]
        else:
            new_block = block_content + '\n' + kw_line

        new_text = new_text[:block_start] + new_block + new_text[block_end:]

    output_file.write_text(new_text, encoding='utf-8')
    print(f"\n已输出至：{output_file}")


if __name__ == '__main__':
    main()
