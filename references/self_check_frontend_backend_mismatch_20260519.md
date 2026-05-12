# SelfCheck.vue 前后端链路修复（2026-05-19）

## 问题

前端 SelfCheck.vue 下拉框显示"No data"，用户无法选择审查进行自检。

## 根因

两个不同问题叠加：

### 问题1：API 端点不存在

- `GET /api/reviews` 返回 **405 Method Not Allowed**
- reviews.py 只有 `POST /start_review`，没有列表 GET 端点

**修复**：新增 `GET /reviews` 端点（返回前50条，按 id 倒序）

### 问题2：getProjects() 返回格式不匹配

- `api.getProjects()` 返回结构：`{items, total, page, page_size}`（分页格式）
- SelfCheck.vue 遍历方式：`for (const p of data)` → 遍历 dict 的 key（items/total/page/page_size），永远取不到审查数据

```javascript
// 错误写法
const res = await api.getProjects()
for (const p of res) { ... }  // p = "items", "total", "page"...
```

**修复**：改为直接调用 `GET /reviews`（返回数组），review 下拉框正常显示（ID + 状态 + 缺陷数）

## FastAPI Pydantic 序列化坑

`response_model=list[ReviewResponse]` 对 datetime/enum 字段序列化时报 500（bcrypt 检测）：
```
TypeError: PetersonsEnum.values() is not JSON serializable
```

**绕过方式**：手动 dict 序列化
```python
results = []
for r in query.all():
    results.append({
        "id": r.id,
        "status": r.status.value if hasattr(r.status, 'value') else r.status,
        ...
    })
return results
```

## SQLite FIELD() 函数不存在

排序时用了 `FIELD(status, 'completed', 'processing', 'failed')`，SQLite 不支持：
```sql
ORDER BY FIELD(status, 'completed', 'processing', 'failed')
```

**修复**：改用 `CASE WHEN`
```sql
ORDER BY CASE status
    WHEN 'completed' THEN 1
    WHEN 'processing' THEN 2
    WHEN 'failed' THEN 3
    ELSE 4 END
```

## SQLAlchemy 2.0 dict(r) 报错

```python
# 报错
dict(r)  # TypeError: cannot convert dictionary update sequence element...
# 修复
dict(r._mapping)
```

## 后续发现的新问题（2026-05-19 补充）

### 问题3：`api.request()` 方法不存在

**报错**：`api.request is not a function`

**根因**：`api` 是 axios 包装对象（`axios.create()`），只有 `get/post/put/delete` 具名方法，没有泛型 `request()` 方法。

**修复**：在 `api/index.js` 中为每个端点定义具名方法：
```js
// api/index.js
getReviews() {
  return api.get('/reviews')
},
getSelfCheckResult(reviewId) {
  return api.get(`/rules/self-check/${reviewId}`)
},
runSelfCheck(reviewId) {
  return api.post(`/rules/self-check/run/${reviewId}`)
},
```

### 问题4：`api.get is not a function`

**报错**：`api.get is not a function`

**根因**：`api` 是包装对象，不是 axios 实例，没有泛型 `.get()` / `.post()` 方法。

**修复**：同问题3，全部使用具名方法，不使用泛型调用。

### 问题5：判决映射逻辑错误（全显示"不实"）

**症状**：缺陷明细表全部显示"✗不实"，但统计卡片显示"17个存疑，20个属实"。

**根因**：`buildDefects()` 判断条件用了英文 `'hit'/'doubt'/'miss'`，但后端返回的是中文 `'属实'/'存疑'/'不实'`：
```js
// ❌ 错误
verdict: d.verify_result?.verdict === 'hit' ? 'hit' : ...  // 永远不成立

// ✅ 正确
const v = raw.verify_result?.verdict
verdictLabel: v === '属实' ? '✓属实' : v === '存疑' ? '?存疑' : '✗不实'
verdict: v === '属实' ? 'hit' : v === '存疑' ? 'doubt' : 'miss'
```

### 问题6：自检结果两种格式未统一

**症状**：review #35 有 `db_results` 格式（直接来自 DB）和 `json_report.defects` 格式（组装后）两种，字段结构完全不同。

**差异**：
| 字段 | json_report | db_results |
|------|-------------|------------|
| verdict | `verify_result.verdict`（中文） | `verdict`（直接在顶层，中文） |
| verify_summary | `verify_result.summary` | `verify_keywords` |
| description | `description` | `description` |

**修复**：写 `normalizeDefect()` 统一处理：
```js
function normalizeDefect(raw) {
  if (raw.verify_result) {
    const v = raw.verify_result.verdict
    return {
      verdict: v === '属实' ? 'hit' : v === '存疑' ? 'doubt' : 'miss',
      verdict_label: v === '属实' ? '✓属实' : v === '存疑' ? '?存疑' : '✗不实',
      verify_summary: raw.verify_result.summary || '',
    }
  } else {
    return {
      verdict: raw.verdict === '属实' ? 'hit' : raw.verdict === '存疑' ? 'doubt' : 'miss',
      verdict_label: raw.verdict === '属实' ? '✓属实' : raw.verdict === '存疑' ? '?存疑' : '✗不实',
      verify_summary: raw.verify_keywords || raw.verify_summary || '',
    }
  }
}
```

## 相关 Commits

- `eia-review-agent`：371be44 → b4ee030（SelfCheck.vue + reviews.py + rules.py 修复）
- `eia-review-skill`：7cdf6b4（self_check.py rule_id 回填）

## 验证

```bash
curl -s http://localhost:8001/api/reviews | python3 -c "import sys,json; data=json.load(sys.stdin); print(f'共 {len(data)} 条审查')"
```
