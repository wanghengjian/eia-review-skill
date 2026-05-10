# 批量规则修改操作记录（2026-05-15）

## 场景

为审核规则库.md中62条B类规则批量增加"✅降级条件"段落。

## 技术方案

**核心约束**：规则库是按行存储的Markdown文件，每条规则块用标题行`###`标识。修改时必须避免行号偏移导致后续插入位置错乱。

**操作流程**：

### Step 1：扫描所有B类规则行号

```python
import re

with open('审核规则库.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找到所有B类规则标题行
b_rule_lines = []
for i, line in enumerate(lines):
    if re.match(r'^### B-\d{3}-\d{2}', line.strip()):
        b_rule_lines.append(i)
```

### Step 2：计算每条规则的块范围

```python
blocks = []
for idx, rule_line in enumerate(b_rule_lines):
    # 块结束：下一条规则行-1，或文件末尾
    end = b_rule_lines[idx + 1] - 1 if idx + 1 < len(b_rule_lines) else len(lines) - 1
    blocks.append((rule_line, end, lines[rule_line].strip()))
```

### Step 3：从后向前插入（避免行号偏移）

```python
for rule_line, end, title in reversed(blocks):
    # 找到该规则块的末行（审核步骤结束后）
    block_text = ''.join(lines[rule_line:end+1])
    # 定位插入点：在块末行（通常是审核步骤的最后一行）之后
    insert_pos = end  # 插入到end行之后（即end+1位置）
    
    # 模板前加\n确保与下一规则标题之间有换行
    template = f"\n\n✅ 降级条件：\n..."
    
    lines.insert(insert_pos + 1, template)  # +1因为insert在位置前
```

## 踩坑记录

### 坑1：正则块匹配失败

```python
# 错误做法：rsplit只切一次，'B-001-01'切出'B-001'
prefix = line.rsplit('-', 1)[0]  # 'B-001-01' → 'B-001' ❌
```

**解决**：改用逐行扫描+行索引，不依赖正则块匹配。

### 坑2：模板末尾缺换行符

```python
# 错误：template末尾无\n，导致与下一规则标题直接粘连
template = f"✅ 降级条件：..."  # ❌ 无\n
lines[end] = lines[end'].rstrip() + template  # 导致下一行###变成同一段落
```

**解决**：template前加`\n`，不用后追加：
```python
template = f"\n\n✅ 降级条件：...\n"
lines.insert(insert_pos + 1, template)
```

### 坑3：块边界错误（吞掉B-001组）

当规则组`B-001-01~04`只有标题`### B-001-01`而没有`### B-001-02/03/04`时，rsplit切出的`B-001`不在模板字典中。

**解决**：完全放弃正则块匹配，改用行索引扫描+B-001~B-015分组模板。

## 分组模板（B-001~B-015）

每组B类规则有专属的降级条件内容，按检查主题定制：

| 规则组 | 降级条件主题 |
|--------|-------------|
| B-001 | 产业政策依据充分 |
| B-002 | 评价等级判定准确 |
| B-003 | 标准适用性充分 |
| B-004 | 规划符合性论证 |
| B-005 | 数值一致性±5%容差 |
| B-006 | 工程内容完整性 |
| B-007 | 选址可行性论证 |
| B-008 | 产能规模数据一致 |
| B-009 | 公参程序合法有效 |
| B-010 | 监测数据来源合规 |
| B-011 | 预测模式参数合理 |
| B-012 | 环保投资估算合理 |
| B-013 | 进度计划可实现 |
| B-014 | 总量来源合规 |
| B-015 | 风险防范措施完备 |

## 验证方法

```bash
# 统计✅出现次数（应为62条B类规则）
grep -c "^✅" 审核规则库.md

# 确认B类规则均有✅（过滤B-005-02/B-008-02原有）
grep "^### B-" 审核规则库.md | wc -l  # 应为62
grep "B-005-02\|B-008-02" 审核规则库.md | grep "✅"  # 原有2条也有

# 检查换行正常（下一行不是###）
grep -A1 "^✅" 审核规则库.md | grep "^--$" | wc -l  # 验证✅后有分隔线
```

## git push后台进程处理

批量commit后通常带大量修改（本次+391行），git push可能超时：

```bash
# 检查push状态
ps aux | grep git | grep push  # 或
jobs -l  # 如果在同一个shell

# 超时处理：commit已在本地，重新push
cd ~/.hermes/skills/eia-quick-review
git push origin main
```

commit本地已确认完整，超时只影响远程，不丢数据。
