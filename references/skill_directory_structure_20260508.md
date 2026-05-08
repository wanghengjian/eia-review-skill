# Skill 目录结构与运行时文件路径（2026-05-08）

## 目录对照

| 路径 | 用途 | Git追踪 |
|------|------|---------|
| `skill/reference/`（单数） | **运行时规则库**，后端审核引擎读取此处 | **不追踪**（gitignore） |
| `skill/references/`（复数） | 分析文档（验证报告、调试记录） | 不追踪，已移至 workspace/docs/references/ |
| `workspace/docs/` | 本地工作文档 | .gitignore 排除 |

## 后端加载规则的实际路径

`backend/app/main.py` line 88:
```python
skill_scripts = hermes_root / "skills" / "eia-quick-review" / "scripts"
rules_file = skill_scripts / "reference" / "审核规则库.md"
```

代码写的是 `scripts/reference/`，但该目录不存在。Python 的 `Path` 操作在目录不存在时不会报错，最终文件查找 fallback 到 `skill/reference/`（因为 `scripts/` 本身也不包含 `reference/` 子目录）。

**结论**：运行时规则库实际读取的是 `skill/reference/`。

## 规则库更新流程

1. 修改规则后 → **必须同步到 `skill/reference/`**（不是 workspace/docs/）
2. 推送 GitHub 备份
3. 重启后端（规则在 startup 事件加载，非热更新）

```bash
# 同步示例
cp workspace/docs/审核规则库-细则补充.md \
   ~/.hermes/skills/eia-quick-review/reference/审核规则库-细则补充.md
```

## 已识别的路径噪音

- `scripts/reference/` — 不存在，代码写了这个路径但实际 fallback 到 `skill/reference/`
- `skill/references/`（复数）— 历史遗留，分析文档已迁出
- `workspace/docs/references/` — 当前分析文档存放位置，gitignore 排除
