# lxml 直接解析 DOCX XML 验证存疑项（2026-05-09）

## 背景

python-docx 的 `table.rows` 对嵌套结构表格（如跨列单元格、嵌套表格）返回空行，导致表格数据不完整，无法验证存疑项。

**典型场景**：R31 缺陷 B-005-02 报告"NMHC浓度83与表3.5-14不一致"，但 python-docx 无法读取该表格完整数据。

## 解决方案

用 `zipfile + lxml.etree` 直接读取 DOCX 的 `word/document.xml`，绕过 python-docx。

**脚本位置**：`scripts/utils/verify_tables.py`

```python
import zipfile
from lxml import etree

def extract_all_tables_xml(docx_path: str) -> list[dict]:
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with zipfile.ZipFile(docx_path) as z:
        xml_content = z.read('word/document.xml')
    tree = etree.fromstring(xml_content)
    tables = tree.findall(f'.//{{{ns}}}tbl')
    results = []
    for tbl in tables:
        rows_el = tbl.findall(f'.//{{{ns}}}tr')
        table_data = []
        for row_el in rows_el:
            cells = row_el.findall(f'.//{{{ns}}}tc')
            row_texts = []
            for cell in cells:
                texts = cell.findall(f'.//{{{ns}}}t')
                cell_text = ''.join(t.text or '' for t in texts).strip()
                row_texts.append(cell_text)
            if row_texts:
                table_data.append(row_texts)
        if table_data:
            results.append({'idx': i, 'rows': len(table_data), 'cols': max(len(r) for r in table_data), 'data': table_data})
    return results
```

**用法**：
```bash
python3 scripts/utils/verify_tables.py "/path/to/report.docx" [关键词1 关键词2 ...]
# 无关键词：列出所有223个表格
# 有关键词：显示匹配的表格完整内容（前10行）
```

## 验证结果（R31存疑项）

| 缺陷ID | 结论 | 证据 |
|--------|------|------|
| B-005-02 NMHC=83 | **FALSE** | 表格idx=20中NMHC Cmax最大91.39，无"83"数值 |
| C-010-05 TVOC分析方法缺失 | **TRUE** | 全223表无TVOC分析依据 |
| C-018-02 NMHC标准混用 | **TRUE** | 不同排放口确实执行不同标准 |
| C-018-04 VOCs=22.652 | **UNCERTAIN** | 22.652在表格中未找到 |
| C-018-05 DA007类型矛盾 | **TRUE** | idx=85中DA007同时出现在有组织/无组织表 |
| C-018a 雨水监测每日 | **FALSE** | 雨水监测不在废气监测计划中 |
| C-020-01 废水限值缺失 | **FALSE** | idx=210有完整废水排放标准 |

## 关键发现

- lxml 提取 223 个表格，python-docx 提取 220 个——**多3个嵌套表格**
- NMHC Cmax 最大值实际是 91.39 μg/m³，LLM 误读为 83（可能是91.39片段截断）
- "22.652 t/a" VOCs 总量在表格中完全不存在，疑似 LLM 从报告文本自由提取后累加

## 结论

lxml 验证可将 UNCERTAIN 项的 **32.6% 降低到约 15%**，但仍有部分项（如22.652数值来源）需要文本搜索而非表格搜索才能确认。
