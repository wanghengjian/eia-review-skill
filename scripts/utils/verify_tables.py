#!/usr/bin/env python3
"""
直接解析DOCX的XML，绕过python-docx的表格读取限制。

原理：python-docx的table.rows对嵌套结构表格（如跨列单元格、嵌套表格）
返回空行，但lxml直接读取XML的<w:tbl>结构可以完整提取。

用法：
    python3 verify_tables.py <报告书.docx> [关键词1 关键词2 ...]
    python3 verify_tables.py report.docx NMHC 发酵 3.5-14

输出：找到包含关键词的表格完整内容（前10行）
"""

import sys
import zipfile
import json
import re
from pathlib import Path
from lxml import etree


def extract_all_tables_xml(docx_path: str) -> list[dict]:
    """
    用lxml直接解析DOCX XML，提取所有表格数据。
    返回: [{idx, rows, cols, data: [[cell1, cell2, ...], ...]}, ...]
    """
    docx_path = Path(docx_path)
    if not docx_path.exists():
        print(f"文件不存在: {docx_path}")
        return []

    with zipfile.ZipFile(docx_path) as z:
        xml_content = z.read('word/document.xml')

    tree = etree.fromstring(xml_content)
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    # 找到所有表格
    tables = tree.findall(f'.//{{{ns}}}tbl')
    results = []

    for i, tbl in enumerate(tables):
        # 提取每行
        rows_el = tbl.findall(f'.//{{{ns}}}tr')
        table_data = []

        for row_el in rows_el:
            # 同一行可能跨列，需要把所有单元格（含跨列的虚格）展平
            cells = row_el.findall(f'.//{{{ns}}}tc')
            row_texts = []
            for cell in cells:
                # 获取单元格内所有文本
                texts = cell.findall(f'.//{{{ns}}}t')
                cell_text = ''.join(t.text or '' for t in texts).strip()
                row_texts.append(cell_text)
            if row_texts:
                table_data.append(row_texts)

        if not table_data:
            continue

        # 估算列数（取最长行）
        max_cols = max(len(row) for row in table_data)

        results.append({
            'idx': i,
            'rows': len(table_data),
            'cols': max_cols,
            'data': table_data
        })

    return results


def find_tables_by_keywords(docx_path: str, keywords: list[str]) -> list[dict]:
    """
    找到包含指定关键词的表格，支持表格索引/表名/段落号搜索。
    搜索范围：每行前3个单元格的文本（通常是表头）
    """
    tables = extract_all_tables_xml(docx_path)
    matched = []

    kw_pattern = '|'.join(re.escape(k) for k in keywords)

    for t in tables:
        # 拼接表格前3行的文本用于快速匹配
        header_lines = []
        for row in t['data'][:3]:
            header_lines.append(' '.join(row[:5]))

        header_text = ' | '.join(header_lines)
        full_text = json.dumps(t['data'], ensure_ascii=False)

        if re.search(kw_pattern, header_text) or re.search(kw_pattern, full_text):
            matched.append(t)

    return matched


def find_table_by_index(docx_path: str, table_index: int) -> dict | None:
    """按表格索引获取单个表格"""
    tables = extract_all_tables_xml(docx_path)
    for t in tables:
        if t['idx'] == table_index:
            return t
    return None


def print_table(t: dict, max_rows: int = 10):
    """格式化打印表格"""
    if not t:
        print("  (未找到)")
        return

    print(f"\n{'='*60}")
    print(f"  表格 idx={t['idx']}  ({t['rows']}行 × {t['cols']}列)")
    print('='*60)

    for i, row in enumerate(t['data'][:max_rows]):
        row_str = ' | '.join(str(c).ljust(20) for c in row)
        prefix = '  HDR' if i == 0 else f'  {i:3d}'
        print(f"{prefix}: {row_str}")

    if t['rows'] > max_rows:
        print(f"  ... (共{t['rows']}行，显示前{max_rows}行)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    docx_path = sys.argv[1]
    keywords = sys.argv[2:] if len(sys.argv) > 2 else []

    print(f"读取报告书: {docx_path}")

    if keywords:
        print(f"搜索关键词: {keywords}")
        matched = find_tables_by_keywords(docx_path, keywords)
        print(f"\n找到 {len(matched)} 个匹配的表格:")
        for t in matched:
            print_table(t)
    else:
        # 无关键词：列出所有表格
        tables = extract_all_tables_xml(docx_path)
        print(f"\n报告书共有 {len(tables)} 个表格")
        for t in tables[:20]:
            header = ' | '.join(t['data'][0][:3]) if t['data'] else ''
            print(f"  [{t['idx']:3d}] {t['rows']}行 {t['cols']}列: {header[:60]}")
        if len(tables) > 20:
            print(f"  ... (共{len(tables)}个表格)")


if __name__ == '__main__':
    main()
