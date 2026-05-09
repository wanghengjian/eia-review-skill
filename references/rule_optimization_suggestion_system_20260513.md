# 规则优化建议系统（2026-05-13）

## 背景

**问题**：规则设计时写的"适用章节"与报告实际写法不一致，导致假阳性。

例如 C-019 公众参与规则：`适用章节：概述, 公众参与`，但实际报告中公众参与内容在"结论"章节，LLM 审查"结论"章节时该规则不会传入 prompt。

## 完整数据流

```
LLM 审查 → findings（缺陷）
          → rule_coverage（每规则×每章节：has_content/keywords_found/content_summary）

审查完成后 → process_rule_optimization() 后处理
          → 扫描 has_content=false 的规则
          → 用 keywords 全文检索相关章节
          → 生成 optimization_suggestions（存 DB）

Web 端 → /rule-suggestions 页面
       → pending 列表展示
       → 采纳：自动更新规则库 + 同步到 skill 目录
       → 拒绝
```

## 后端改动（commit 83ea0c2）

### llm_client.py（prompt 修改）

在 JSON 输出格式说明中新增：

```
{
  "findings": [...],
  "rule_coverage": [
    {
      "rule_id": "C-019-01",
      "has_content": 1,          // 0=无相关内容, 1=有相关内容
      "keywords_found": ["公众参与", "公示"],  // 本章找到的关键词
      "content_summary": "本章第3节包含完整的两次公示内容..."
    },
    ...
  ]
}
```

### reviews.py（后处理 + API）

**process_rule_optimization()**：审查完成后调用，扫描 rule_coverage，生成建议并存 DB。

**API 端点**：
- `GET /api/reviews/rule-suggestions` — 列表（支持 review_id/status/rule_id 过滤）
- `POST /api/reviews/rule-suggestions/{id}/apply` — 采纳（更新 Rule 表 + 同步到 skill 目录）
- `POST /api/reviews/rule-suggestions/{id}/reject` — 拒绝

**`_sync_rule_to_skill()`**：采纳后同步到 `~/.hermes/skills/eia-quick-review/reference/审核规则库.md` 和 `_拆分版.md`。

### models.py

新增 `RuleOptimizationSuggestion` 模型（对应 `rule_optimization_suggestions` 表）。

### schemas.py

新增 `RuleOptimizationSuggestionResponse` Schema。

## 前端改动（commit 061f4e5）

- `RuleSuggestions.vue` — 规则优化建议页面
- `Layout.vue` — 侧边栏新增"规则优化"导航（admin 专属）
- `api/index.js` — 新增 `getRuleSuggestions`/`applyRuleSuggestion`/`rejectRuleSuggestion` 方法

## 数据库

手动建表：
```sql
CREATE TABLE IF NOT EXISTS rule_optimization_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    rule_id VARCHAR(20) NOT NULL,
    rule_name VARCHAR(200),
    chapter_num VARCHAR(10) NOT NULL,
    chapter_name VARCHAR(100) NOT NULL,
    has_content INTEGER NOT NULL DEFAULT 0,
    keywords_found TEXT,
    content_summary TEXT,
    suggested_chapters TEXT,
    suggestion_text TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    created_at DATETIME,
    applied_at DATETIME
);
```

## API 错误排查

**"api.get is not a function"**
- 原因：`import api from '@/api'` 后直接调用 `api.get('/path', ...)`，但 api 导出的是命名方法对象而非 axios 实例
- 解决：在 `api/index.js` 中添加具名方法（如 `getRuleSuggestions`），前端用 `api.getRuleSuggestions(params)` 调用

## 验证方法

1. 发起新审查（必须，新审查才会产生 rule_coverage 数据）
2. 审查完成后查 DB：`SELECT * FROM rule_optimization_suggestions WHERE status='pending'`
3. 登录 Web → 管理菜单 → 规则优化 → 查看待处理建议
4. 点击"采纳" → 验证 skill 目录下的规则库文件已更新
5. 重启后端 → 下次审查新规则生效
