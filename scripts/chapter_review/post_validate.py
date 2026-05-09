#!/usr/bin/env python3
"""
后处理校验层 - post_validate.py

对LLM输出的缺陷进行二次校验，将与预扫描结果矛盾的缺陷打上flag，
辅助人工复审，不直接删除缺陷。
"""
import re
from typing import Any, Dict, List


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
            "flags": f.get("_validate_flags", []),
            "flag_type": f.get("_flag_type", "NEEDS_REVIEW"),
        })
    return summary
