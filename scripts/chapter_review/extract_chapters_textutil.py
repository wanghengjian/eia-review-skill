#!/usr/bin/env python3
"""
环评报告书内容提取脚本

功能：
1. 提取DOCX/DOC/PDF文件的文本和表格
2. 按一级标题拆分章节
3. 输出完整文本、表格数据、项目信息

用法：
    python3 extract_chapters_textutil.py -n "项目名称" -d "报告书.docx" -o output_dir
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# 表格编号正则：表1.4-1, 表 1.4-2, 表3.3-1, 表5.4-2 等（表号前可能有空格）
TABLE_NUM_PATTERN = re.compile(r'表\s*(\d+(?:[.-]\d+)+)')

# 尝试导入文档处理库
try:
    from docx import Document
except ImportError:
    Document = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None
# 附件类关键词（模块级常量，splitChapters 和 detect_chapter_number 共用）
ATTACHMENT_KEYWORDS = ['附件', '附表', '附录']


def convert_doc_to_docx(doc_path: str) -> str:
    """使用textutil或LibreOffice将DOC转换为DOCX"""
    doc_path = Path(doc_path)
    if not doc_path.exists():
        raise FileNotFoundError(f"文件不存在: {doc_path}")

    # 如果已经是docx，直接返回
    if doc_path.suffix.lower() == '.docx':
        return str(doc_path)

    temp_dir = tempfile.mkdtemp()
    output_path = Path(temp_dir) / f"{doc_path.stem}.docx"

    # 候选转换工具，按优先级
    converters = []

    # 1. LibreOffice (最可靠，对老版DOC支持最好)
    libre_paths = [
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
        '/usr/bin/libreoffice',
        '/usr/local/bin/libreoffice',
    ]
    for lp in libre_paths:
        if Path(lp).exists():
            converters.append(('libreoffice', lp))
            break

    # 2. textutil (macOS自带，对某些DOC支持有限)
    if Path('/usr/bin/textutil').exists():
        converters.append(('textutil', '/usr/bin/textutil'))

    if not converters:
        # 检查同目录是否有现成的docx（可能是用户另传的）
        existing_docx = doc_path.with_suffix('.docx')
        if existing_docx.exists():
            return str(existing_docx)
        raise RuntimeError("无可用的DOC转换工具（需要LibreOffice或Xcode命令行工具）")

    last_error = ""
    for tool_name, tool_path in converters:
        try:
            if tool_name == 'libreoffice':
                result = subprocess.run(
                    [tool_path, '--headless', '--convert-to', 'docx',
                     '--outdir', str(temp_dir), str(doc_path)],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                if result.returncode != 0:
                    last_error = f"LibreOffice转换失败: {result.stderr}"
                    continue
            elif tool_name == 'textutil':
                result = subprocess.run(
                    [tool_path, '-convert', 'docx', '-output', str(output_path), str(doc_path)],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode != 0:
                    last_error = f"textutil转换失败: {result.stderr}"
                    continue

            # 检查输出文件是否存在且非空
            # LibreOffice输出的文件名可能被截断，检查目录下所有新docx
            if not output_path.exists() or output_path.stat().st_size == 0:
                # LibreOffice可能输出不同文件名（长文件名截断），搜索目录
                generated = list(Path(temp_dir).glob('*.docx'))
                if generated:
                    # 取最大的（排除0字节）
                    valid = [f for f in generated if f.stat().st_size > 0]
                    if valid:
                        output_path = max(valid, key=lambda f: f.stat().st_size)

            if output_path.exists() and output_path.stat().st_size > 0:
                return str(output_path)

            last_error = f"{tool_name}转换生成了空文件"
        except subprocess.TimeoutExpired:
            last_error = f"{tool_name}转换超时"
        except FileNotFoundError:
            last_error = f"{tool_name}命令不存在"
        except Exception as e:
            last_error = f"{tool_name}转换异常: {e}"

    # 所有工具都失败了，检查同目录备用
    existing_docx = doc_path.with_suffix('.docx')
    if existing_docx.exists():
        return str(existing_docx)
    raise RuntimeError(f"DOC转换失败（已尝试所有可用工具）: {last_error}")


def _get_para_full_text(para) -> str:
    """获取段落完整文本（包括所有run）"""
    # para.text 只返回直系文本，需要合并所有run
    return ''.join(run.text for run in para.runs if run.text)


def _get_paragraph_style_name(para) -> str:
    """获取段落样式名称（如 'Heading 1', 'Heading 2', 'toc 1' 等）"""
    try:
        pPr = para.find('.//w:pPr', ns)
        if pPr is not None:
            pStyle = pPr.find('.//w:pStyle', ns)
            if pStyle is not None:
                return pStyle.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val') or ''
    except Exception:
        pass
    return ''

def extract_from_docx(docx_path: str) -> Tuple[str, List[Dict], Dict]:
    """
    从DOCX文件提取文本、表格和项目信息。

    按文档顺序遍历 body 元素（段落+表格交替），
    对每个表格标记其所属章节编号（chapter_num），
    便于后续审核时将表格与对应章节一起提交给LLM。

    章节标题识别策略（优先级递减）：
    1. Word 样式（Heading 1）→ 一级章节，新建章节
    2. Word 样式（Heading 2）→ 二级章节，只更新当前章节编号，不新建
    3. Word 样式（toc 1/2）→ 目录，跳过不加入正文
    4. 中文数字章节（"一、XX"）→ 只更新编号不新建（Fallback）
    5. "第X章"格式 → 一级章节（Fallback）
    6. 独立章节名（"概述"等）→ 一级章节（Fallback）
    """
    if Document is None:
        raise ImportError("python-docx库未安装，请运行: pip install python-docx")

    doc = Document(docx_path)
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    body = doc.element.body
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    body_children = list(body)

    full_text = []
    tables = []
    table_id = 0
    seen_table_ids = set()  # 用于全量表格去重（body直接表格 vs 嵌套表格 vs AlternateContent）

    # 当前章节编号（000=序言）
    # _chapter_base: 遇到第一个有效"第X章"H1 时建立基准（= 章节号），非"第X章"H1 基于基准递增
    # 章节号 = _chapter_base + _h1_counter - 1（与 splitChapters 的 _counter 对齐）
    current_chapter = '000'
    _chapter_base = None  # type: int | None
    _h1_counter = [0]     # 非"第X章"H1 的递增计数
    _last_detected_ch = '000'  # 上次检测到的新章节号（用于 Normal fallback 校准）

    for child in body_children:
        if isinstance(child, CT_P):
            para = child  # 直接用 XML 元素，不再从 doc.paragraphs 查
            text = para.text.strip() if para.text else ''
            # 样式名从 pPr> pStyle 获取
            pStyle = para.find('.//w:pPr/w:pStyle', ns)
            style_name = (pStyle.get(f'{{{ns}}}val') or '') if pStyle is not None else ''

            if not text:
                # 空段落（如只有 AlternateContent 渲染对象、无实际文本的段落）：内联提取内嵌表格
                for tbl_el in para.findall('.//w:tbl', ns):
                    if id(tbl_el) not in seen_table_ids:
                        seen_table_ids.add(id(tbl_el))
                        table_data = []
                        for row in tbl_el.findall('.//w:tr', ns):
                            cells = row.findall('.//w:tc', ns)
                            row_data = []
                            for cell in cells:
                                cell_text = ''.join(
                                    t.text or '' for t in cell.findall('.//w:t', ns)
                                ).strip()
                                row_data.append(cell_text)
                            table_data.append(row_data)
                        tables.append({
                            "table_id": table_id,
                            "chapter_num": current_chapter,
                            "data": table_data,
                            "rows": len(table_data),
                            "cols": len(table_data[0]) if table_data else 0,
                        })
                        table_id += 1
                continue

            is_heading1 = style_name == 'Heading 1'
            is_heading2 = style_name == 'Heading 2'
            is_toc = style_name.startswith('toc')

            # TOC 目录内容跳过（正文里不应该出现）
            if is_toc:
                continue

            if is_heading1:
                # TOC 目录标题（H1 样式或文本含"目录"）：
                # 不加入 full_text、不占计数器。
                # splitChapters 靠自身的 toc_range 检测来归类目录内容，
                # extract 不为它分配章节号，确保正文章节编号与 splitChapters 对齐。
                is_toc_heading = text == '目录' or text.endswith('目') and len(text) < 5
                if is_toc_heading:
                    continue

                # 检测是否为附件标题（H1 中含"附件"关键词）
                # H1 附件：设置 current_chapter='012'，后续内容归入附件章节。
                # [H1:附件] 标记由下面的 detect_chapter_number + full_text.append(f'[H1:{text}]') 统一添加，
                # 不要在这里单独添加（会重复）。
                is_attachment_h1 = '附件' in text
                if is_attachment_h1:
                    current_chapter = '012'  # 附件强制为最后一章

                # 检测章节标题类型
                ch_num = detect_chapter_number(text)

                if ch_num == '999':
                    # 非"第X章"格式的 H1（概  述/1总则等）：
                    # - 加入 full_text（splitChapters 据此识别章节边界）
                    # - 基于已建立的基准递增；第一个非"第X章"H1 建立基准 0
                    _h1_counter[0] += 1
                    if _chapter_base is None:
                        _chapter_base = 0
                    current_chapter = f'{_chapter_base + _h1_counter[0] - 1:03d}'
                    full_text.append(f'[H1:{text}]')
                else:
                    # 有效章节 H1（"第X章"格式）：
                    # - 加入 full_text（splitChapters 据此创建正文章节）
                    # - 建立新的章节基准（= 章节号），重置 _h1_counter
                    _chapter_base = int(ch_num)
                    _h1_counter[0] = 0
                    current_chapter = ch_num
                    _last_detected_ch = ch_num
                    full_text.append(f'[H1:{text}]')
            elif is_heading2:
                # Heading 2 → 节内标题，内容归入当前章节
                # 但附件 H2（附件1/附件2/...）是附件章节的起始标记
                if '附件' in text or '附表' in text:
                    current_chapter = '012'  # 附件章节，后续内容归入 012
                full_text.append('[H2:章节]')
                full_text.append(text)
            else:
                # Normal 段落：检测"第X章"文本模式，校准计数器
                # 处理 Word 中章节标题被设为 Normal 样式的情况（与 splitChapters fallback 对齐）
                #
                # TOC 检测（与 splitChapters Phase 1 逻辑一致）：
                # 包含中文数字+顿号+"章"的段落（如"一、总则"）是目录项，不应触发计数器更新
                is_toc_entry_chinese = bool(re.search(r'[一二三四五六七八九十百]+[、，][^第]*章', text))
                if is_toc_entry_chinese:
                    # 目录项：追加到 full_text 但不触发计数器更新
                    full_text.append(text)
                    continue

                ch_num = detect_chapter_number(text)
                # 章节号检测：必须是段落开头出现"第X章"（而非正文中间误匹配）
                # 例如正文引用"根据《XXX》第一章的规定"不应被识别为章节
                is_chapter_start = ch_num != '999' and (
                    text.startswith('第') or
                    bool(re.match(r'^\s*第', text))
                )
                # 附表/附件/附录标题检测（Normal 段落中的章节外标题）
                if '附表' in text or '附件' in text or '附录' in text:
                    # 附件/附录：追加到 full_text 但不触发计数器更新
                    full_text.append(text)
                    continue
                if is_chapter_start and ch_num != _last_detected_ch:
                    # 新的正文章节标题（之前没见过），校准计数器与章节号对齐
                    _h1_counter[0] = int(ch_num)
                    current_chapter = ch_num
                    _last_detected_ch = ch_num
                full_text.append(text)

        elif isinstance(child, CT_Tbl):
            # 全量表格提取：body 直接子表格 + 其单元格内的嵌套表格
            # 去重用 seen_table_ids（处理 AlternateContent: Choice 和 Fallback 指向同一表格对象）
            def _extract_single_table(tbl_el, chapter_num):
                """提取一个表格及其所有嵌套子表格，递归继承 chapter_num"""
                nonlocal table_id
                if id(tbl_el) in seen_table_ids:
                    return
                seen_table_ids.add(id(tbl_el))
                table_data = []
                for row in tbl_el.findall('.//w:tr', ns):
                    cells = row.findall('.//w:tc', ns)
                    row_data = []
                    for cell in cells:
                        cell_text = ''.join(
                            t.text or '' for t in cell.findall('.//w:t', ns)
                        ).strip()
                        row_data.append(cell_text)
                    table_data.append(row_data)
                # 从第一行第一格解析表号（如"表3.3-1" → "3.3-1"）
                table_number = None
                if table_data and table_data[0]:
                    first_cell = table_data[0][0] if table_data[0] else ""
                    m = TABLE_NUM_PATTERN.search(first_cell)
                    if m:
                        table_number = m.group(1)

                tables.append({
                    "table_id": table_id,
                    "chapter_num": chapter_num,
                    "data": table_data,
                    "rows": len(table_data),
                    "cols": len(table_data[0]) if table_data else 0,
                    "table_number": table_number,  # 实际表号，如"3.3-1"
                })
                table_id += 1

                # 递归提取单元格内的嵌套表格
                nested_tbls = tbl_el.findall('.//w:tbl', ns)
                for nested_tbl in nested_tbls:
                    _extract_single_table(nested_tbl, chapter_num)

            # 处理当前 body 直接子表格（包含所有嵌套在单元格内的表格）
            _extract_single_table(child, current_chapter)

    full_text_str = '\n\n'.join(full_text)

    # 兜底：扫描所有 remaining 嵌套表格（如嵌在 AlternateContent 内的表格，
    # 不属于 body 直接子元素，在主循环中未被处理）
    remaining_tbls = body.findall(f'.//{{{ns}}}tbl')
    for tbl_el in remaining_tbls:
        if id(tbl_el) not in seen_table_ids:
            # 找最近的 H1 标题（向前搜索 full_text）
            chapter_num = current_chapter
            # 从 full_text 末尾向前找最近的 [H1:xxx] 作为章节归属
            h1_matches = list(re.finditer(r'\[H1:([^\]]+)\]', full_text_str))
            if h1_matches:
                last_h1 = h1_matches[-1].group(1)
                ch = detect_chapter_number(last_h1)
                if ch != '999':
                    chapter_num = ch
            seen_table_ids.add(id(tbl_el))
            table_data = []
            for row in tbl_el.findall('.//w:tr', ns):
                cells = row.findall('.//w:tc', ns)
                row_data = []
                for cell in cells:
                    cell_text = ''.join(
                        t.text or '' for t in cell.findall('.//w:t', ns)
                    ).strip()
                    row_data.append(cell_text)
                table_data.append(row_data)
            tables.append({
                "table_id": table_id,
                "chapter_num": chapter_num,
                "data": table_data,
                "rows": len(table_data),
                "cols": len(table_data[0]) if table_data else 0,
                "table_number": None,  # 兜底表格不解析表号
            })
            table_id += 1

    # 提取项目信息
    project_info = extract_project_info(full_text_str, tables)

    return full_text_str, tables, project_info


def extract_from_pdf(pdf_path: str) -> Tuple[str, List[Dict], Dict]:
    """从PDF文件提取文本、表格和项目信息"""
    if fitz is None:
        raise ImportError("PyMuPDF库未安装，请运行: pip install pymupdf")

    doc = fitz.open(pdf_path)
    full_text_parts = []
    tables = []
    table_id = 0

    for page_num, page in enumerate(doc):
        # 提取文本
        text = page.get_text()
        if text.strip():
            full_text_parts.append(text.strip())

        # 提取表格（简化版）
        # PyMuPDF的表格提取比较复杂，这里用简单方法
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") == 0:  # 文本块
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        # 检查是否是表格形式的文本（连续的短行）
                        pass

    full_text_str = '\n\n'.join(full_text_parts)

    # 提取项目信息
    project_info = extract_project_info(full_text_str, tables)

    return full_text_str, tables, project_info


def extract_tables_by_keyword(tables: List[Dict], keywords: List[str], min_rows: int = 2) -> List[Dict]:
    """在所有表格中搜索包含指定关键词的表格，返回匹配表格列表"""
    results = []
    for t in tables:
        all_text = ''.join([''.join(cell) for row in t.get('data', []) for cell in row])
        header_text = ''.join([''.join(cell) for cell in t.get('header', [])])
        combined = header_text + all_text
        if sum(1 for kw in keywords if kw in combined) >= len(keywords) and len(t.get('data', [])) >= min_rows:
            results.append(t)
    return results


def _extract_line_value(text: str, keyword: str, max_chars: int = 4000) -> str:
    """在文档开头区域搜索关键词所在行，提取冒号/：后的值，只取到行尾或第一个标点为止

    max_chars: 从头开始搜索的最大字符数，默认4000（覆盖项目信息区域）
    """
    scope = text[:max_chars]
    for line in scope.split('\n'):
        if keyword in line:
            # 找到关键词，取后面的内容
            idx = line.find(keyword) + len(keyword)
            rest = line[idx:].strip()
            # 去掉开头的冒号/：/空格/图
            rest = re.sub(r'^[：:\s图]+', '', rest)
            # 截取到第一个标点符号为止
            m = re.match(r'([^，。、；；\n（）()]{0,100})', rest)
            if m:
                val = m.group(1).strip()
                # 去掉末尾的空白字符
                val = re.sub(r'[。\s]+$', '', val)
                return val
    return ""


def _get_cell_str(cell) -> str:
    """提取单元格字符串值，支持 str 或 list[str] 类型"""
    if isinstance(cell, list):
        return ''.join(cell).strip()
    return str(cell).strip()


def _find_col_by_keyword(row: list, keywords: list) -> int:
    """在一行（表头行）中找到包含任意关键词的单元格索引，找不到返回-1"""
    for ci, cell in enumerate(row):
        ct = ''.join(cell) if isinstance(cell, list) else str(cell)
        if any(kw in ct for kw in keywords):
            return ci
    return -1


def extract_project_info(text: str, tables: List[Dict] = None) -> Dict[str, Any]:
    """从文本和表格中提取项目基本信息。

    策略：
    1. 找到"2.1 项目基本情况"或"第二章 项目工程概况"区域（正文中的章节，非目录）
    2. 在该区域内用 _extract_line_value 提取各项字段
    3. 投资信息从全文搜索
    """
    # ── 1. info dict 初始化 ─────────────────────────────────────────────
    info: Dict[str, Any] = {
        "project_name": "",
        "company": "",
        "evaluation_level": "",
        "construction_nature": "",
        "location": "",
        "total_investment": "",
        "environmental_investment": "",
        "construction_content_scale": "",
        "wastewater_pollutants": [],
        "exhaust_gas_pollutants": [],
        "noise_sources": [],
        "hazardous_waste": [],
    }

    # ── 2. 找到项目概况章节的正文区域 ─────────────────────────────────
    # 搜索章节标题（正文中的，不是目录里的）
    chapter_start = text.find('\n第二章 项目工程概况\n')
    if chapter_start < 0:
        chapter_start = text.find('\n2.1 项目基本情况\n')
    if chapter_start < 0:
        # fallback: 找"（1）项目名称："这种格式的位置
        chapter_start = text.find('（1）项目名称：')
    if chapter_start < 0:
        chapter_start = 0

    # 章节结束：下一章（第三章）
    chapter_end = text.find('\n第三章', chapter_start + 10)
    if chapter_end < 0:
        chapter_end = len(text)

    section_text = text[chapter_start:chapter_end]
    print(f"[extract_project_info] 项目概况区域: 位置 {chapter_start} ~ {chapter_end} ({len(section_text)} 字)")

    # ── 3. 字段提取（支持（1）前缀的格式） ──────────────────────────────
    def extract_value(search_text: str, keyword: str) -> str:
        """支持（1）项目名称：xxx 格式，也支持项目名称：xxx"""
        for line in search_text.split('\n'):
            if keyword not in line:
                continue
            idx = line.find(keyword) + len(keyword)
            rest = line[idx:].strip()
            # 去掉开头的冒号/：/空格/图
            rest = re.sub(r'^[：:\s图（）()]+', '', rest)
            # 截取到第一个标点符号为止，但允许顿号和斜杠（用于数字、单位）
            m = re.match(r'([^，。、；；\n（）()·]+(?:[㎡m³m3\d][^\s，。、；；\n（）()]*)?)', rest)
            if m:
                val = m.group(1).strip()
                val = re.sub(r'[。\s]+$', '', val)
                return val
        return ""

    info["project_name"] = extract_value(section_text, "项目名称：")
    info["company"] = extract_value(section_text, "建设单位：")
    info["construction_nature"] = extract_value(section_text, "项目性质：")
    info["location"] = extract_value(section_text, "项目选址：")
    if not info["location"]:
        info["location"] = extract_value(section_text, "建设地点：")

    # 环评类别
    eval_lv = extract_value(section_text, "环评类别：")
    if not eval_lv:
        if '报告书' in text[:3000]:
            eval_lv = "环境影响评价报告书"
        elif '报告表' in text[:3000]:
            eval_lv = "环境影响评价报告表"
    info["evaluation_level"] = eval_lv

    # 投资：全文搜索"项目投资"行
    for line in section_text.split('\n'):
        if '项目投资：' in line or '项目投资为' in line:
            # 格式：项目投资为400万元，环保投资为400万元
            m = re.search(r'项目投资[为：]?\s*([0-9万亿元]+万)', line)
            if m:
                info["total_investment"] = m.group(1)
            m2 = re.search(r'环保投资[为：]?\s*([0-9万亿元]+万)', line)
            if m2:
                info["environmental_investment"] = m2.group(1)
            break
    # fallback：全文搜"总投资"（避开目录区）
    if not info["total_investment"]:
        idx_total = text.find('总投资', 5000)
        if idx_total >= 0:
            line = text[max(0, idx_total-20):idx_total+50]
            m = re.search(r'([0-9]+万)', line)
            if m:
                info["total_investment"] = m.group(1)
    if not info["environmental_investment"]:
        idx_env = text.find('环保投资', 5000)
        if idx_env >= 0:
            line = text[max(0, idx_env-20):idx_env+50]
            m = re.search(r'([0-9]+万)', line)
            if m:
                info["environmental_investment"] = m.group(1)

    # 建设内容和规模
    # 优先从概述区域（工业废水集中处理系统段落，位置2200-2400）提取
    overview_start = text.find('工业废水集中处理系统', 0, 30000)
    if overview_start < 0:
        overview_start = 0
    overview_end = text.find('\n第一章', overview_start)
    if overview_end < 0:
        overview_end = overview_start + 5000
    overview_text = text[overview_start:overview_end]

    # 从概述区域提取
    for line in overview_text.split('\n'):
        if '处理规模为' in line or '50m³' in line or '50m3' in line:
            # 格式：处理规模为50m³/d，占地面积为290㎡。工艺流程：...
            m = re.search(r'([0-9m³/d／/平方㎡m2m²·]+[^。\n]*)', line)
            if m:
                info["construction_content_scale"] = m.group(1).strip()
                break
            # fallback：直接找包含数字和m³/d的行
            m2 = re.search(r'([0-9m³/d／/平方㎡m2m²·]+[^\n]*)', line)
            if m2:
                info["construction_content_scale"] = m2.group(1).strip()

    # fallback：从2.2 项目建设内容区域（当前逻辑）
    if not info["construction_content_scale"]:
        ch2_start = section_text.find('2.2')
        ch2_end = section_text.find('\n2.3', ch2_start) if ch2_start >= 0 else -1
        if ch2_end < 0:
            ch2_end = section_text.find('\n第三章', ch2_start) if ch2_start >= 0 else -1
        if ch2_start >= 0 and ch2_end > ch2_start:
            content_text = section_text[ch2_start:ch2_end]
            for line in content_text.split('\n'):
                if '设计处理规模' in line or '处理规模' in line:
                    m = re.search(r'处理规模[为：\s]*([0-9m³/d／/平方㎡m2]+[^\n]{0,80})', line)
                    if m:
                        val = m.group(1).strip()
                        val = val.split('。')[0]   # 去掉"工艺流程"之后的内容
                        info["construction_content_scale"] = val
                        break

    # ── 4. 表格：污染物数据 ────────────────────────────────────────────
    if not tables:
        tables = []

    # 3.1 废水污染物
    ww_tables = extract_tables_by_keyword(tables, ['污染因子', '进水浓度'])
    for ww_t in ww_tables:
        rows = ww_t.get('data', [])
        if len(rows) < 3:
            continue
        header = rows[0]
        col_factor = _find_col_by_keyword(header, ['污染因子'])
        col_in = _find_col_by_keyword(header, ['进水'])
        col_day = _find_col_by_keyword(header, ['日排放', '日产生'])
        col_year = _find_col_by_keyword(header, ['年排放', '年产生'])
        if col_factor < 0:
            continue
        for row in rows[1:]:
            if len(row) <= col_factor:
                continue
            factor = _get_cell_str(row[col_factor])
            factor = factor.strip()
            if not factor or factor in ['水量', '废水量', '合计', '序号', '表', '项目']:
                continue
            in_val = _get_cell_str(row[col_in]) if col_in >= 0 and col_in < len(row) else ''
            day_val = _get_cell_str(row[col_day]) if col_day >= 0 and col_day < len(row) else ''
            year_val = _get_cell_str(row[col_year]) if col_year >= 0 and col_year < len(row) else ''
            if not any(re.search(r'\d', v) for v in [in_val, day_val, year_val]):
                continue
            info["wastewater_pollutants"].append({
                "因子": factor,
                "进水浓度(mg/L)": in_val,
                "日排放量(kg/d)": day_val,
                "年排放量(t/a)": year_val,
            })

    # 3.2 废气污染物
    ex_tables = extract_tables_by_keyword(tables, ['NH3', '产生量', 't/a'])
    for ex_t in ex_tables:
        rows = ex_t.get('data', [])
        if len(rows) < 3:
            continue
        header = rows[0]
        col_factor = _find_col_by_keyword(header, ['NH3', 'H2S', '非甲烷总烃', '污染物', '污染成分'])
        col_gen = _find_col_by_keyword(header, ['产生量'])
        col_emit = _find_col_by_keyword(header, ['有组织', '排放量', '排放'])
        if col_factor < 0:
            col_factor = 0
        seen = set()
        for row in rows[1:]:
            if len(row) <= col_factor:
                continue
            factor_raw = row[col_factor]
            factor = _get_cell_str(factor_raw)
            if not factor or factor in ['产排情况', '序号', '排放源', '有组织', '无组织', '类别', '执行标准']:
                continue
            if factor in seen:
                continue
            gen_val = _get_cell_str(row[col_gen]) if col_gen >= 0 and col_gen < len(row) else ''
            emit_val = _get_cell_str(row[col_emit]) if col_emit >= 0 and col_emit < len(row) else ''
            if gen_val or emit_val:
                seen.add(factor)
                info["exhaust_gas_pollutants"].append({
                    "因子": factor,
                    "年产生量(t/a)": gen_val,
                    "排放量(t/a)": emit_val,
                })

    # 3.3 噪声
    noise_tables = extract_tables_by_keyword(tables, ['噪声', 'dB'], min_rows=3)
    for nt in noise_tables:
        rows = nt.get('data', [])
        if len(rows) < 3:
            continue
        header = rows[0]
        col_name = _find_col_by_keyword(header, ['名称', '声源', '设备'])
        col_db = _find_col_by_keyword(header, ['dB', '声压', '源强'])
        if col_name < 0:
            col_name = 0
        if col_db < 0:
            col_db = 2
        for row in rows[2:]:
            if len(row) <= max(col_name, col_db):
                continue
            name = _get_cell_str(row[col_name])
            db = _get_cell_str(row[col_db])
            if name and name not in ['序号', ''] and ('dB' in db or re.search(r'\d', db)):
                info["noise_sources"].append({
                    "名称": name,
                    "声压级(dB(A))": db,
                })

    # 3.4 危险废物
    hw_tables = extract_tables_by_keyword(tables, ['危险废物', '产生量', 't/a'])
    for ht in hw_tables:
        rows = ht.get('data', [])
        if len(rows) < 2:
            continue
        header = rows[0]
        col_name = _find_col_by_keyword(header, ['名称'])
        col_code = _find_col_by_keyword(header, ['代码', '类别'])
        col_amount = _find_col_by_keyword(header, ['产生量', '排放量'])
        if col_name < 0:
            continue
        for row in rows[1:]:
            if len(row) <= col_name:
                continue
            name = _get_cell_str(row[col_name])
            if not name or name in ['序号', '合计', '类别']:
                continue
            code = _get_cell_str(row[col_code]) if col_code >= 0 and col_code < len(row) else ''
            amount = ''
            if col_amount >= 0 and col_amount < len(row):
                amount_raw = row[col_amount]
                amount = _get_cell_str(amount_raw)
            if not re.match(r'^[\d.]+$', amount) and col_amount < 0:
                for cell in row:
                    cell_str = _get_cell_str(cell)
                    if re.match(r'^[\d.]+$', cell_str) and 0 < float(cell_str) < 10000:
                        amount = cell_str.strip()
                        break
            if amount:
                info["hazardous_waste"].append({
                    "名称": name,
                    "代码": code,
                    "产生量(t/a)": amount,
                })

    return info


def splitChapters(full_text: str) -> Tuple[Dict[str, Dict], str]:
    """
    按一级标题拆分章节，同时提取目录（TOC）内容。

    返回 (chapters, toc_content)：
    - chapters: 章节编号 → {content, line_count}
    - toc_content: 目录部分原始文本（用于TOC完整性验证）

    核心策略（两阶段）：
    阶段1：识别目录范围（从"目录"到第一个 [H1:xxx] 标记之间）
    阶段2：目录范围外的内容，以 [H1:xxx] 标记建立章节，顺序分配编号 000 开始

    Word 样式章节标记（在 extract_from_docx 中注入）：
    - [H1:标题] → 新建章节，标记行内容即章节标题
    - [H2:章节] → 节内标题，标记行后一行（H2 标题文本）跳过不写入正文
    """

    chapters = {}

    # ── 正则与常量 ───────────────────────────────────────────────────────────
    chapter_heading_pattern = re.compile(r'^第([一二三四五六七八九十百千万零]+)章\s*(.*)$')
    # 中文数字章节格式：如"一、项目由来"、"二、环境影响评价..."
    chinese_num_chapter_pattern = re.compile(r'^([一二三四五六七八九十百]+)、(.+)$')
    known_standalone_chapters = {
        '概述', '项目由来', '建设项目由来', '前言', '总论',
    }
    attachment_keywords = ATTACHMENT_KEYWORDS

    lines = full_text.split('\n')

    # ── 阶段1：预扫描，定位目录范围 ─────────────────────────────────────
    # toc_range: [start_idx, end_idx)（含start不含end），None表示无目录
    toc_range = None  # type: tuple[int, int] | None

    def _is_toc_marker(stripped: str) -> bool:
        """检测一行是否是目录标题（纯文本或 [H1:目录] 样式标记）"""
        s = stripped.replace(' ', '').replace('\u3000', '')
        # 纯文本"目录" / "目录/" 等
        if s == '目录' or (s.endswith('目录') and len(s) < 10):
            return True
        # [H1:目录] 标记（Word 样式 TOC 标题）
        if stripped == '[H1:目录]' or stripped == '[H1:目录/]' or stripped == '[H1:目录 ]':
            return True
        return False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if _is_toc_marker(stripped):
            # 找到"目录"标题，扫描直到遇到第一个 [H1:xxx] 正文标记（且不是目录本身）
            toc_start = i
            toc_end = min(i + 50, len(lines))  # 最多扫描50行，防止整篇被归为目录
            j = i + 1
            while j < toc_end:
                nxt = lines[j].strip()
                if nxt.startswith('[H1:') and not _is_toc_marker(nxt):
                    toc_range = (toc_start, j)
                    break
                j += 1
            else:
                # 未在50行内找到[H1:]正文标记：只把"目录"所在行归为目录
                toc_range = (toc_start, i + 1)
            break

    # ── 阶段2：正文分割（目录范围外的所有内容） ──────────────────────────
    current_chapter = None
    current_content = []
    toc_content = []

    _counter = [0]
    def _next_chapter_num() -> str:
        num = _counter[0]
        _counter[0] += 1
        return f"{num:03d}"

    def _is_attachment_start(text: str) -> bool:
        """判断一行是否是附件章节的起始（进入附件模式）"""
        # 精确匹配：单独的"附件" / "附表" / "附录"
        if text in ('附件', '附表', '附录'):
            return True
        # 模式：附件/附表/附录 + 数字/中文数字（如"附件1"、"附表二"、"附录：第一部分"）
        if re.match(r'^(附件|附表|附录)[\d一二三四五六七八九十百]+', text):
            return True
        # 模式：附件/附表/附录 + 冒号（如"附件："、"附表："）
        if re.match(r'^(附件|附表|附录)[：:]', text):
            return True
        return False

    # 辅助：某行索引是否在目录范围内
    def _in_toc(idx: int) -> bool:
        return toc_range is not None and toc_range[0] <= idx < toc_range[1]

    # ── 阶段2：正文分割为章节 ──────────────────────────────────────────
    # 逻辑（按优先级）：
    #   ① 目录范围内 → 收集到 toc_content
    #   ② [H2:章节] 标记 → 设置 skip_next_line，跳过下一行（H2 节标题）
    #   ③ [H1:xxx] 标记 → 新建章节，marker 行内容即章节标题
    #   ④ 附件项 → 进入附件模式
    #   ⑤ 正文内容 → 加入当前章节
    # Word 样式（H1/H2）在 extract_from_docx 中注入，决定了本章的章节结构
    heading1_pattern = re.compile(r'^\[H1:([^\]]*)\]$')  # 含标题文本
    heading2_pattern = re.compile(r'^\[H2:章节\]$')       # 不含标题文本
    known_standalone = {'概述', '项目由来', '建设项目由来', '前言', '总论'}

    # 标记当前行的下一行是否应跳过（H2 节标题，不是正文）
    skip_next_line = False

    def _chapter_heading_for_split(text: str) -> bool:
        """
        判断一行是否是章节标题（用于 splitChapters 阶段2）。

        本文档由 Word 样式决定章节：
        - Heading 1 段落 → [H1:标题] 标记，新建章节，标记行本身就是标题
        - Heading 2 段落 → [H2:章节] 标记，节内标题，内容归入当前章节
        """
        # [H1:xxx] 标记 → 新章节（标记行内容即章节标题）
        if heading1_pattern.match(text):
            return True
        # [H2:章节] 标记 → 不触发新章节
        if heading2_pattern.match(text):
            return False
        return False

    # 提取 [H1:标题] 中的标题文本
    def _h1_title(text: str) -> str:
        m = heading1_pattern.match(text)
        return m.group(1) if m else text

    in_attachment = False
    lines = full_text.split('\n')
    i = 0  # 重置：phase1 的 for 循环已将 i 走到末尾，需要归零
    while i < len(lines):
        line = lines[i]
        line_stripped = line.strip()
        i += 1

        # ① 目录范围内 → 不分配章节编号
        if _in_toc(i - 1):
            toc_content.append(line_stripped)
            continue

        # ② skip_next_line：H2 节标题行应跳过，不进正文
        if skip_next_line:
            skip_next_line = False
            continue

        # ③ [H2:章节] 标记 → 跳过（附件模式下也是 Word 样式注入标记，不保留）
        # 附件模式：跳过之前剥掉 trailing blank，避免[H2:章节]前多余的空行
        if heading2_pattern.match(line_stripped):
            if in_attachment and current_content and current_content[-1] == '':
                current_content.pop()
            continue

        # ④ blank line：保留空行（分隔附件条目结构）
        if not line_stripped:
            skip_next_line = False
            current_content.append('')
            continue

        # ⑤ [H1:标题] 标记 → 新建章节，marker行内容即章节标题
        if heading1_pattern.match(line_stripped):
            # 保存当前章节
            if current_chapter and current_content:
                content = '\n'.join(current_content)
                if content.strip():
                    chapters[current_chapter] = {"content": content, "line_count": len(current_content)}
                current_content = []
            current_chapter = _next_chapter_num()
            # 章节标题取自 marker 内的文本（如"概  述"、"1总则"）
            h1_title = _h1_title(line_stripped)
            current_content = [h1_title]
            # 如果是附件标题，进入附件模式，后续所有内容都归入本章
            if h1_title.startswith('附件') or h1_title.startswith('附表') or h1_title.startswith('附录'):
                in_attachment = True
            continue

        # ⑥ 附件项 → 非H1的附件条目（如"附件1-..."、"附图1-..."）直接追加到当前章节内容
        # 注意：不要创建新章节！附件内容应归入其前面最近的章节
        if _is_attachment_start(line_stripped):
            current_content.append(line_stripped)
            continue

        # ⑥' 独立章节名（"概述"、"项目由来"、"前言"等）→ 新建章节
        if line_stripped in known_standalone:
            if current_chapter and current_content:
                content = '\n'.join(current_content)
                if content.strip():
                    chapters[current_chapter] = {"content": content, "line_count": len(current_content)}
                current_content = []
            current_chapter = _next_chapter_num()
            current_content = [line_stripped]
            continue

        # ⑦' 退出附件模式后遇到"1总则"纯阿拉伯数字格式 → 创建新章节（29号线等文档）
        # 严格判断：数字(1-10)后紧跟汉字，排除"15号线"、"2024年"、"1.1"、"1、"等
        if not in_attachment and not chapter_heading_pattern.match(line_stripped):
            m_arb = re.match(r'^(\d+)(?=[\u4e00-\u9fff])(?!号)([\u4e00-\u9fff].*)$', line_stripped)
            if m_arb:
                try:
                    arabic = int(m_arb.group(1))
                    # 章节号范围：1-10（排除工程编号如"15号线"、"29号线"，以及年份"2024"）
                    if 1 <= arabic <= 10:
                        if current_chapter and current_content:
                            content = '\n'.join(current_content)
                            if content.strip():
                                chapters[current_chapter] = {"content": content, "line_count": len(current_content)}
                            current_content = []
                        current_chapter = _next_chapter_num()
                        current_content = [line_stripped]
                        continue
                except ValueError:
                    pass

        # ⑦ 退出附件模式后遇到"第X章" → 创建新章节（正文文档流）
        if not in_attachment and chapter_heading_pattern.match(line_stripped):
            suffix = chapter_heading_pattern.match(line_stripped).group(2).strip()
            if suffix and suffix[-1].isdigit():
                pass  # TOC条目，跳过
            else:
                if current_chapter and current_content:
                    content = '\n'.join(current_content)
                    if content.strip():
                        chapters[current_chapter] = {"content": content, "line_count": len(current_content)}
                    current_content = []
                current_chapter = _next_chapter_num()
                current_content = [line_stripped]
                continue

        # ⑧ 正文段落 → 追加到当前章节
        if not _in_toc(i - 1):
            current_content.append(line_stripped)

    # 保存最后一个章节
    if current_chapter and current_content:
        content = '\n'.join(current_content)
        if content.strip():
            chapters[current_chapter] = {"content": content, "line_count": len(current_content)}

    # ── 兜底：附件章节遗漏检测 ─────────────────────────────────────────────
    # 旧版 full_text（无 [H1:附件] 标记）时，附件内容会归入最后一个章节。
    # 如果正文末尾有附件关键词但没有 012，说明附件被归入了末章，
    # 此时不需要创建单独的 012 章节（以免重复），只需确保完整性检查通过即可。
    # 由于 _run_completeness_check 检查的是 "012" in chapter_nums，
    # 如果 ch012 不存在但正文末尾有附件内容，应视为完整性通过（附件已包含在末章中）。
    pass  # 兜底逻辑已由 _run_completeness_check 端处理，此处无需修改 chapters

    return chapters, '\n'.join(toc_content)


def _chinese_to_arabic(cn: str) -> int:
    """将中文数字转换为阿拉伯数字（支持任意大小）"""
    cn_nums = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '百': 100, '千': 1000, '万': 10000
    }

    if not cn:
        return 0

    # 纯个位数
    if len(cn) == 1:
        return cn_nums.get(cn, 0)

    result = 0
    temp = 0

    for char in cn:
        val = cn_nums.get(char, 0)
        if val >= 10:
            if temp == 0:
                temp = 1
            result += temp * val
            temp = 0
        else:
            temp = val

    result += temp
    return result if result > 0 else 0


def _make_chapter_num(arabic_num: int) -> str:
    """将阿拉伯数字转为3位字符串章节编号"""
    # 001-009: 标准前导章节, 010-099: 标准章节, 100+: 超纲章节
    return f"{min(arabic_num, 999):03d}"


# 标准章节名称 → 标准编号（用于判断是否超纲）
def detect_chapter_number(t: str) -> str:
    """
    根据章节标题文本返回章节编号字符串。
    支持：
    - "第X章" 格式（如"第一章"）
    - "一、XX" 中文数字前缀（如"一、项目由来"）
    - "1总则" 纯阿拉伯数字格式（29号线等文档）
    - 附件关键词
    未知标题返回 '999'。
    """
    # 第X章格式（如"第一章"、"第1章"）→ 返回编号
    m = re.match(r'^第([一二三四五六七八九十百千万零]+)章', t)
    if m:
        return _make_chapter_num(_chinese_to_arabic(m.group(1)))
    # 中文数字章节前缀（如"一、项目由来"、"二、概述"）
    m_cn = re.match(r'^([一二三四五六七八九十百]+)、', t)
    if m_cn:
        arabic = _chinese_to_arabic(m_cn.group(1))
        if arabic > 0:
            return _make_chapter_num(arabic)
    # 纯阿拉伯数字章节格式（如"1总则"、"2建设项目工程概况"、"10环境影响评价结论"，无"第"字前缀）
    # 判断逻辑：数字后紧跟汉字(?=[\u4e00-\u9fff]) 且不是"号"字(?!号)，章节号范围1-10
    # 排除："15号线"/"29号线"（数字+号+线，工程编号）、"2024年"（数字+年）、"1.1"/"1、"（数字+分隔符）
    m_arb = re.match(r'^(\d+)(?=[\u4e00-\u9fff])(?!号)([\u4e00-\u9fff].*)$', t)
    if m_arb:
        try:
            arabic = int(m_arb.group(1))
            # 章节号范围：1-10（排除工程编号如"15号线"、"29号线"，以及年份"2024"）
            if 1 <= arabic <= 10:
                return _make_chapter_num(arabic)
        except ValueError:
            pass
    # 独立附件检测（用于 extract_from_docx 识别附件标题）
    is_att = any((t == kw or t.startswith(kw)) for kw in ATTACHMENT_KEYWORDS)
    if is_att:
        return '012'
    return '999'


def save_output(output_dir: str, project_name: str, full_text: str,
                tables: List[Dict], project_info: Dict, chapters: Dict[str, Dict],
                toc_content: str = "") -> str:
    """保存输出文件"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 文件名前缀
    prefix = re.sub(r'[^\w\u4e00-\u9fff-]', '_', project_name)[:50]

    # 1. 保存完整文本
    full_text_file = output_path / f"{prefix}_full_text.txt"
    full_text_file.write_text(full_text, encoding='utf-8')

    # 2. 保存表格数据
    tables_file = output_path / f"{prefix}_tables.json"
    tables_file.write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding='utf-8')

    # 3. 保存目录（TOC）
    if toc_content:
        toc_file = output_path / "toc.txt"
        toc_file.write_text(toc_content, encoding='utf-8')

    # 4. 保存项目信息
    info_file = output_path / "项目信息.json"
    info_file.write_text(json.dumps(project_info, ensure_ascii=False, indent=2), encoding='utf-8')

    # 5. 保存各章节
    chapters_dir = output_path / "chapters"
    chapters_dir.mkdir(exist_ok=True)

    for num, data in sorted(chapters.items()):
        chapter_file = chapters_dir / f"chapter_{num}.txt"
        chapter_file.write_text(data["content"], encoding='utf-8')

    # 6. 保存章节映射表（章节编号→标题，方便排查章节分配问题）
    chapter_mapping = {}
    for num, data in sorted(chapters.items()):
        first_line = data["content"].split('\n')[0].strip() if data["content"] else ""
        chapter_mapping[num] = {
            "title": first_line,
            "line_count": data.get("line_count", 0),
            "char_count": len(data["content"])
        }
    mapping_file = output_path / "chapter_mapping.json"
    mapping_file.write_text(json.dumps(chapter_mapping, ensure_ascii=False, indent=2), encoding='utf-8')

    return str(output_path)


def main():
    parser = argparse.ArgumentParser(description='环评报告书内容提取工具')
    parser.add_argument('-n', '--project', required=True, help='项目名称')
    parser.add_argument('-d', '--docx', required=True, help='报告书文件路径（DOCX/DOC/PDF）')
    parser.add_argument('-o', '--output', required=True, help='输出目录')
    parser.add_argument('--session', default='review_001', help='会话标识')

    args = parser.parse_args()

    # 验证文件
    doc_path = Path(args.docx)
    if not doc_path.exists():
        print(f"错误: 文件不存在: {doc_path}", file=sys.stderr)
        sys.exit(1)

    # 创建输出目录
    output_dir = Path(args.output) / args.project / args.session
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"正在提取文件: {doc_path}")
    print(f"输出目录: {output_dir}")

    try:
        # 根据文件类型提取
        suffix = doc_path.suffix.lower()

        if suffix == '.pdf':
            print("提取PDF文件...")
            full_text, tables, project_info = extract_from_pdf(str(doc_path))
        elif suffix in ['.docx', '.doc']:
            # DOC需要先转换
            if suffix == '.doc':
                print("转换DOC为DOCX...")
                docx_path = convert_doc_to_docx(str(doc_path))
            else:
                docx_path = str(doc_path)

            print("提取DOCX文件...")
            full_text, tables, project_info = extract_from_docx(docx_path)
        else:
            print(f"错误: 不支持的文件格式: {suffix}", file=sys.stderr)
            sys.exit(1)

        print(f"提取完成: {len(full_text)} 字符, {len(tables)} 个表格")

        # 章节分割
        print("正在分割章节...")
        chapters, toc_content = splitChapters(full_text)
        print(f"分割完成: {len(chapters)} 个章节")

        # 保存输出
        print("保存输出文件...")
        output_path = save_output(
            str(output_dir / "extract"),
            args.project,
            full_text,
            tables,
            project_info,
            chapters,
            toc_content
        )

        print(f"\n提取完成！")
        print(f"输出目录: {output_path}")
        print(f"\n项目信息:")
        print(f"  项目名称: {project_info.get('project_name', '未知')}")
        print(f"  建设单位: {project_info.get('company', '未知')}")
        print(f"  评价等级: {project_info.get('evaluation_level', '未知')}")
        print(f"\n章节列表:")
        for num in sorted(chapters.keys()):
            print(f"  {num}: {len(chapters[num]['content'])} 字符")

    except Exception as e:
        print(f"错误: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
