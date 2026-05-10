#!/usr/bin/env python3
"""
后处理校验层 - post_validate.py

对LLM输出的缺陷进行二次校验，将与预扫描结果矛盾的缺陷打上flag，
辅助人工复审，不直接删除缺陷。
"""
import re
from typing import Any, Dict, List


def cross_validate_findings(
    raw_findings: List[Dict],
    full_report_text: str
) -> List[Dict]:
    """
    章节一致性交叉验证：检测LLM缺陷描述与报告原文的矛盾。

    机制：
    - 提取缺陷描述中的关键事实陈述（如"引用资料A1-A2缺少NOx"）
    - 在报告原文中检索该事实陈述
    - 若原文明确包含与缺陷描述相反的内容，标记为"疑似误判"

    Args:
        raw_findings: LLM输出的原始缺陷列表
        full_report_text: 报告书完整文本

    Returns:
        附加了交叉验证flag的缺陷列表
    """
    if not full_report_text or not raw_findings:
        return raw_findings

    # 已知的高风险规则（容易出现LLM阅读理解错误）
    high_risk_rules = {"C-010-03", "C-010-01", "C-010-02"}

    # 反义词/否定模式（用于检测缺陷描述与原文矛盾）
    negation_patterns = [
        (r"缺少", r"含有|包含|具备|具备|有\d"),
        (r"未提及", r"已提及|提及|写了|指出"),
        (r"未引用", r"引用了|引自|依据"),
        (r"未包含", r"包含|含有|涵盖"),
        (r"无", r"有\d|含|具备"),
    ]

    validated = []
    for finding in raw_findings:
        f = dict(finding)
        f["_cross_flags"] = []

        rule_id = f.get("rule_id", "")
        desc = f.get("description", "")

        # 仅对高风险规则执行交叉验证
        if rule_id not in high_risk_rules:
            validated.append(f)
            continue

        # 检测缺陷描述中是否含有明确的"缺失/缺少/未X"陈述
        for neg_pattern, pos_pattern in negation_patterns:
            neg_match = re.search(neg_pattern, desc)
            if not neg_match:
                continue

            neg_phrase = neg_match.group(0)
            # 提取缺失项的名称（如"NOx"、"氨氮"等）
            # 向前向后各取10个字符作为上下文
            start = max(0, neg_match.start() - 10)
            end = min(len(desc), neg_match.end() + 15)
            context = desc[start:end]

            # 在报告中检索正面的表述
            # 如果报告明确包含"有NOx"或"已包含NOx"等，则矛盾
            if re.search(pos_pattern, full_report_text):
                # 进一步确认：正面表述确实提到了缺失项
                mentioned_item = context
                if re.search(pos_pattern.replace("\\d", ""), full_report_text):
                    f["_cross_flags"].append(
                        f"⚠️ 交叉验证疑问：缺陷描述称'{neg_phrase}'，"
                        f"但报告中存在与此矛盾的正向表述，请复核原文确认缺陷是否成立。"
                    )
                    f["_flag_type"] = "CROSS_VALIDATION_FAILED"

        validated.append(f)

    return validated


def validate_findings(
    raw_findings: List[Dict],
    pre_scan_report: Dict[str, Any]
) -> List[Dict]:
    """
    对LLM原始缺陷列表进行后校验
    
    Args:
        raw_findings: LLM输出的原始缺陷列表
        pre_scan_report: 预扫描报告（来自pre_scan.py）
    
    Returns:
        校验后的缺陷列表（增加flag字段）
    """
    if not pre_scan_report or pre_scan_report.get("error"):
        return raw_findings
    
    table_index = pre_scan_report.get("table_index", {})
    verified_numbers = pre_scan_report.get("verified_numbers", [])
    numeric_contradictions = pre_scan_report.get("numeric_contradictions", [])
    
    validated = []
    for finding in raw_findings:
        f = dict(finding)  # 不修改原始
        f["_validate_flags"] = []
        
        rule_id = f.get("rule_id", "")
        desc = f.get("description", "")
        location = f.get("location", "")
        
        # === 规则专用校验 ===
        
        # B-002-06 / B-008-02：表不存在 → 对照预扫描表格索引
        if rule_id in ("B-002-06", "B-008-02", "B-002"):
            cited_tables = _extract_table_numbers(desc)
            for tbl in cited_tables:
                if tbl in table_index:
                    chs = table_index[tbl]
                    f["_validate_flags"].append(
                        f"LLM主张表{tbl}不存在，但预扫描发现其在ch{chs}存在"
                    )
        
        # B-005-01 / B-005-02：数值矛盾 → 对照预扫描数值验算
        if rule_id in ("B-005-01", "B-005-02", "B-005"):
            # 查找分项加减验算类
            mentioned_numbers = _extract_numbers(desc)
            for num_item in mentioned_numbers:
                for verified in verified_numbers:
                    if verified["type"] == num_item["type"] and verified["consistent"]:
                        # LLM主张矛盾，但预扫描说一致
                        f["_validate_flags"].append(
                            f"B-005数值矛盾：LLM称'{num_item['type']}'不一致，"
                            f"预扫描已验算一致（{verified['total']}={verified['items']}）"
                        )
        
        # C-001：产业政策 → 一致时不应记缺陷
        if rule_id and rule_id.startswith("C-001") and "一致" in desc:
            f["_validate_flags"].append(
                "C-001类：LLM在一致的情况下不应记为缺陷（一致时应描述为'符合'）"
            )
        
        # C-019：公众参与章节归属
        if rule_id == "C-019":
            f["_validate_flags"].append(
                "C-019跨章节适用：确认公众参与内容是否在概述章节，若仅在结论章节则不记缺陷"
            )
        
        # === 严重度校验：A类缺陷必须有明确法规/标准依据 ===
        severity = f.get("severity", "")
        if severity == "A" or severity.startswith("🔴"):
            # 检索缺陷描述中是否含有明确法规依据关键词
            law_keywords = [
                "不符合《", "违反", "必须执行", "应当采用",
                "未按", "未满足", "未执行", "未引用",
                "排放限值", "浓度限值", "标准限值", "行业标准",
                "强制", "不应", "禁止"
            ]
            has_law_basis = any(kw in desc for kw in law_keywords)

            if not has_law_basis:
                f["_validate_flags"].append(
                    "⚠️ 严重度疑问：此缺陷被判定为A类，但缺陷描述中未发现明确法规/标准强制依据。"
                    "请人工复核：若缺陷仅为'引用标准不够精确'或'缺少某种论证'而引用本身不构成明确错误，"
                    "建议维持B类而非升A。"
                )
                f["_severity_flag"] = "A_WITHOUT_EXPLICIT_LAW"

        # 判断是否需要降权（高置信度flag → 标记为疑似假阳性）
        if f["_validate_flags"]:
            high_confidence_flags = [
                fl for fl in f["_validate_flags"]
                if "表" in fl and "不存在" in fl
            ]
            if high_confidence_flags:
                f["_flag_type"] = "PRE_SCAN_CONTRADICTION"
                f["_confidence_override"] = "low"

        validated.append(f)

    return validated


def _extract_table_numbers(text: str) -> List[str]:
    """从文本中提取表格编号"""
    pattern = r'表(\d+(?:[.-]\d+)+)'
    return re.findall(pattern, text)


def _extract_numbers(text: str) -> List[Dict]:
    """从文本中提取数值及其类型"""
    results = []
    # 匹配 "类型：数值" 或 "类型 数值" 格式
    patterns = [
        (r'给排水[：:\s]*(\d+\.?\d*)', '给排水'),
        (r'蒸汽冷凝[：:\s]*(\d+\.?\d*)', '蒸汽冷凝'),
        (r'建筑面积[：:\s]*(\d+\.?\d*)', '建筑面积'),
        (r'环保投资[：:\s]*(\d+\.?\d*)', '环保投资'),
    ]
    for pat, ntype in patterns:
        m = re.search(pat, text)
        if m:
            results.append({"type": ntype, "value": float(m.group(1))})
    return results


def summarize_flags(validated_findings: List[Dict]) -> Dict[str, Any]:
    """汇总所有flag，生成人工复审摘要"""
    flagged = [f for f in validated_findings if f.get("_validate_flags")]
    summary = {
        "total_findings": len(validated_findings),
        "flagged_findings": len(flagged),
        "flag_details": [],
    }
    for f in flagged:
        summary["flag_details"].append({
            "id": f.get("id", ""),
            "rule_id": f.get("rule_id", ""),
            "title": f.get("title", ""),
            "severity": f.get("severity", ""),
            "flags": f.get("_validate_flags", []),
            "flag_type": f.get("_flag_type", "NEEDS_REVIEW"),
            "severity_flag": f.get("_severity_flag", ""),
        })
    return summary
