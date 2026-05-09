#!/usr/bin/env python3
"""
LLM调用封装模块

用于调用LLM进行环评报告书审查，基于MiniMax M2模型。
"""

import json
import os
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str
    model: str
    usage: Dict[str, int]
    finish_reason: str


class EIA_LLMReview:
    """环评报告书LLM审查器"""

    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://api.minimax.chat/v1",
        model: str = "MiniMax-Text-01",
        timeout: int = 120,
        prompt_output_dir: str = None
    ):
        """
        初始化LLM审查器

        Args:
            api_key: API密钥
            base_url: API基础URL
            model: 模型名称
            timeout: 超时时间(秒)
            prompt_output_dir: LLM prompt 输出目录(设为路径则每次调用都保存prompt到文件，None则不保存)
        """
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.prompt_output_dir = prompt_output_dir

    def review_chapter(
        self,
        chapter_num: str,
        chapter_name: str,
        chapter_content: str,
        rules_text: str = "",
        tables: str = "",
        context: str = "",
        pre_scan_injection: str = ""
    ) -> Dict[str, Any]:
        """
        审查单个章节

        Args:
            chapter_num: 章节编号
            chapter_name: 章节名称
            chapter_content: 章节内容
            rules_text: 适用规则原文(Markdown文本片段)
            tables: 相关表格数据
            context: 上下文(前一章节的末尾内容)
            pre_scan_injection: 预扫描结果文本(表格索引、数值验算等)
        """
        # 构建提示词
        prompt = self._build_chapter_review_prompt(
            chapter_num, chapter_name, chapter_content, rules_text, tables, context,
            pre_scan_injection
        )

        # 保存prompt到文件(如果配置了输出目录)
        if self.prompt_output_dir:
            self._save_prompt(chapter_num, chapter_name, prompt)

        # 调用LLM
        response = self._call_llm(prompt)

        # 解析响应
        return self._parse_review_response(response, chapter_num, chapter_name)

    def global_review(
        self,
        full_text: str,
        project_info: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        全局审查(一次性LLM调用)

        Args:
            full_text: 完整文本
            project_info: 项目基本信息

        Returns:
            审查结果JSON
        """
        prompt = self._build_global_review_prompt(full_text, project_info)
        response = self._call_llm(prompt)
        return self._parse_global_review_response(response)

    def _build_chapter_review_prompt(
        self,
        chapter_num: str,
        chapter_name: str,
        chapter_content: str,
        rules_text: str,
        tables: str,
        context: str,
        pre_scan_injection: str = ""
    ) -> str:
        """构建章节审查提示词(优化版)"""

        prompt = f"""你是一位资深的环评技术审查专家。请严格按照以下要求对环评报告书章节进行技术审查。

## 角色与任务
- 角色：熟悉环评法律法规、技术导则(HJ系列)、产业政策、规划符合性要求的资深专家
- 任务：根据给定的【章节内容】、【相关表格】和【审查规则库】，逐条分析是否存在问题
- 规则适用原则：仅审查与本章节内容直接相关的规则。若不相关，在分析中说明跳过原因

{pre_scan_injection if pre_scan_injection else ''}

## 审查规则库(Markdown原文)
{rules_text if rules_text else "(无特定规则，请进行通用技术审查)"}

## 输入信息
### 章节信息
- 章节编号：{chapter_num}
- 章节名称：{chapter_name}

### 上下文(前一章节末尾，用于保持连续性)
{context if context else "(无)"}

### 章节内容
{chapter_content}

### 相关表格(如有)
{tables if tables else "(无表格数据)"}

## 输出要求
1. **严格输出 JSON 格式**，不要包含任何额外文字、注释或 markdown 代码块标记(如 ``` json)
2. **字段说明**：
   - `chapter_num`：章节编号
   - `chapter_name`：章节名称
   - `findings`：问题列表，每条问题包含：
     - `id`：规则编号 + 序号(如"B-001-1"，无则留空)
     - `title`：问题标题(10-20字)
     - `severity`：high / medium / low(与规则类别对应：B类→high，C类→medium，A类→low)
     - `confidence`：high / medium / low(高=有文字证据支撑)
     - `location`：问题所在位置(精确到章节小标题或段落首句)
     - `description`：问题描述(50-150字，说明违反了什么、缺了什么)
     - `rule_id`：对应规则编号(无则填"通用")
     - `rule_name`：对应规则名称(无则填"通用审核")
     - `suggestion`：修改建议(50-150字)
   - `summary`：本章审查总结(100字以内)
   - `has_major_issues`：存在 high 缺陷时为 true，否则 false
3. **若无问题**：`findings` 数组为空，`has_major_issues` 为 false
4. **审核依据**：必须引用章节原文或表格中的具体内容作为证据

## 审查步骤(思维链)
请按以下步骤思考并输出：
1. 阅读章节内容，提取关键信息(项目类型、选址、规模、环评类别判定、三线一单分析等)
2. 解析输入的规则库，提取所有规则及其编号、名称、情形、审核步骤
3. 逐条判断每条规则是否适用于本章节：
   - 若适用，按该规则的审核步骤逐一检查
   - 若不适用，跳过(内部记录，不输出)
4. 对发现的问题，按输出格式记录，确保有充分证据
5. 汇总 summary，判断 has_major_issues

现在，请对以下内容进行审查。
"""
        return prompt

    def _build_global_review_prompt(
        self,
        full_text: str,
        project_info: Dict[str, str]
    ) -> str:
        """构建全局审查提示词"""
        project_name = project_info.get("project_name", "未知")
        company = project_info.get("company", "未知")
        evaluation_level = project_info.get("evaluation_level", "未知")

        prompt = f"""你是一位资深的环评技术审查专家。请对以下环评报告书进行全局技术审查。

## 项目信息
- 项目名称: {project_name}
- 建设单位: {company}
- 评价等级: {evaluation_level}

## 审查要点

请重点检查以下方面：

### 1. 章节完整性
- 是否按照HJ 2.1-2016规定的12个标准章节编制
- 各章节是否有实质性内容(非仅有标题)

### 2. 法规标准引用
- 是否正确引用适用的大气/地表水/声/土壤等标准
- 标准年代号是否为现行有效版本
- 深圳地标DB4403/T 548-2024是否正确应用

### 3. 公众参与
- 是否按《环境影响评价公众参与办法》开展公参
- 两次公示时间、内容、方式是否合规

### 4. 关键数据汇总确认
- 评价等级判定是否正确
- 工程规模、污染源强等关键数据是否前后一致

### 5. 较大缺陷快速预警
请特别关注可能导致不予批准的重大缺陷：
- 选址布局规模不符
- 区域环境质量不达标且措施不满足改善要求
- 污染防治措施无法确保达标排放
- 基础资料明显不实、内容重大缺陷

## 报告书文本(部分，选取关键章节)
---
{full_text[:20000]}
---

## 输出要求

请严格按照以下JSON格式输出审查结果：

```json
{{
  "project_name": "{project_name}",
  "overall_findings": [
    {{
      "category": "章节完整性/法规标准/公众参与/数据一致性/重大缺陷预警",
      "severity": "high/medium/low",
      "title": "问题标题",
      "description": "问题描述",
      "suggestion": "修改建议"
    }}
  ],
  "chapter_completeness": {{
    "status": "complete/incomplete",
    "missing_chapters": [],
    "empty_chapters": []
  }},
  "major_issues_warning": true/false,
  "warning_details": "重大缺陷预警说明",
  "recommendation": "整体审核建议"
}}
```
        """
        return prompt

    def _save_prompt(self, chapter_num: str, chapter_name: str, prompt: str):
        """保存LLM prompt到文件(用于回溯调试)"""
        os.makedirs(self.prompt_output_dir, exist_ok=True)
        # 文件名：chapter_{编号}_{章节名}_{时间戳}.txt
        safe_name = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in chapter_name)
        timestamp = time.strftime("%H%M%S")
        filename = f"prompt_ch{chapter_num}_{safe_name}_{timestamp}.txt"
        filepath = os.path.join(self.prompt_output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(prompt)
        # 顺便打印路径方便调试
        print(f"  [prompt saved] {filepath}")

    def _call_llm(self, prompt: str) -> LLMResponse:
        """调用LLM API"""
        import requests

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,  # 较低的temperature以保证一致性
            "max_tokens": 4096
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=self.timeout
            )
            response.raise_for_status()

            result = response.json()

            return LLMResponse(
                content=result["choices"][0]["message"]["content"],
                model=result.get("model", self.model),
                usage=result.get("usage", {}),
                finish_reason=result["choices"][0].get("finish_reason", "")
            )

        except requests.exceptions.Timeout:
            raise TimeoutError(f"LLM调用超时({self.timeout}秒)")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"LLM调用失败: {str(e)}")

    def _parse_review_response(
        self,
        response: LLMResponse,
        chapter_num: str,
        chapter_name: str
    ) -> Dict[str, Any]:
        """解析章节审查响应"""
        try:
            # 尝试提取JSON
            content = response.content

            # 处理可能的markdown代码块
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end]
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end]

            result = json.loads(content.strip())

            # 验证必要字段
            if "findings" not in result:
                result["findings"] = []
            if "summary" not in result:
                result["summary"] = ""
            if "has_major_issues" not in result:
                result["has_major_issues"] = any(
                    f.get("severity") == "high" for f in result["findings"]
                )

            return result

        except json.JSONDecodeError as e:
            # JSON解析失败，返回空结果
            return {
                "chapter_num": chapter_num,
                "chapter_name": chapter_name,
                "findings": [],
                "summary": f"审查完成(解析响应失败): {str(e)}",
                "has_major_issues": False,
                "parse_error": str(e),
                "raw_response": response.content[:500]
            }

    def _parse_global_review_response(self, response: LLMResponse) -> Dict[str, Any]:
        """解析全局审查响应"""
        try:
            content = response.content

            # 处理可能的markdown代码块
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end]
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end]

            result = json.loads(content.strip())

            # 验证必要字段
            if "overall_findings" not in result:
                result["overall_findings"] = []
            if "chapter_completeness" not in result:
                result["chapter_completeness"] = {"status": "unknown"}
            if "major_issues_warning" not in result:
                result["major_issues_warning"] = any(
                    f.get("severity") == "high" for f in result.get("overall_findings", [])
                )

            return result

        except json.JSONDecodeError as e:
            return {
                "overall_findings": [],
                "chapter_completeness": {"status": "unknown"},
                "major_issues_warning": False,
                "recommendation": f"全局审查完成(解析响应失败)",
                "parse_error": str(e),
                "raw_response": response.content[:500]
            }


# 便捷函数
def review_chapter(
    chapter_num: str,
    chapter_name: str,
    chapter_content: str,
    rules_text: str = "",
    tables: str = "",
    context: str = ""
) -> Dict[str, Any]:
    """便捷函数：审查单个章节"""
    reviewer = EIA_LLMReview()
    return reviewer.review_chapter(
        chapter_num, chapter_name, chapter_content, rules_text, tables, context
    )


def global_review(
    full_text: str,
    project_info: Dict[str, str]
) -> Dict[str, Any]:
    """便捷函数：全局审查"""
    reviewer = EIA_LLMReview()
    return reviewer.global_review(full_text, project_info)


if __name__ == "__main__":
    # 测试
    print("EIA LLM Review 模块已加载")
    print(f"支持的模型: MiniMax-Text-01")
