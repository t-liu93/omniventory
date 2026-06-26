# M5 Step 6 实现简报 — 全局 LIKE 搜索 + SearchProvider 抽象层

## (a) 本轮实现内容

### 总览

实现了 M5 Step 6 规定的全局跨实体搜索功能，包含 `SearchProvider` 协议抽象层、`LikeSearchProvider` 实现、`SearchService` 服务、API 端点 `GET /search`，以及对应的 Pydantic 响应模式。

### 新增文件

| 文件 | 作用 |
|---|---|
| `backend/app/services/search/__init__.py` | 包入口，导出公共接口 |
| `backend/app/services/search/provider.py` | `SearchProvider` 协议 + 结果数据类（`DefinitionHit`/`InstanceHit`/`LocationHit`/`CategoryHit`/`TagHit`/`SearchResults`） |
| `backend/app/services/search/like.py` | `LikeSearchProvider` — 各实体类型的 LIKE 查询实现 |
| `backend/app/services/search/service.py` | `SearchService`（迭代 provider 列表、合并结果） + `build_search_service(db)` 工厂函数 |
| `backend/app/schemas/search.py` | `SearchResponse`、`SearchTotals`、各类型 Hit 模式 |
| `backend/app/api/routes/search.py` | `GET /search?q=&types=&limit=` 路由 |
| `backend/tests/test_m5_step6.py` | 35 个测试 |

### 修改文件

- `backend/app/main.py` — 注册 `search_router`
- `openapi.json` + `frontend/src/api/schema.d.ts` — codegen 更新

### 各实体类型的搜索字段

| 类型 | 搜索字段 |
|---|---|
| `item_definition` | `name` OR `description` OR `custom_fields`（文本 LIKE，覆盖自定义字段值） |
| `stock_instance` | `serial` OR `model_number` OR `manufacturer` OR `custom_fields` 文本 LIKE，**加 `barcodes.code` 关联**（通过 `stock_instances.definition_id == barcodes.definition_id` 外连接） |
| `location` | `name` |
| `category` | `name` |
| `tag` | `name` |

### 关键实现细节

- **LIKE 模式**：复用已有的 `func.lower(col).contains(func.lower(q))` 可移植写法，无 SQLite FTS，无原始 SQL。
- **barcode join**：`outerjoin(Barcode, StockInstance.definition_id == Barcode.definition_id)` + `or_(..., func.lower(Barcode.code).contains(lower_q))` + `.distinct()` + `.options(joinedload(StockInstance.definition))`，避免同一 instance 因多个匹配 barcode 重复出现。
- **per-type 上限**：各类型先获取全部匹配行（无 DB 侧 LIMIT），`total = len(rows)` 写入 `totals`，再 Python 切片 `rows[:limit]` 生成结果列表。
- **空/空白 q**：路由层直接返回空 `SearchResponse`，不触发 DB 查询。
- **types 参数**：逗号分隔，交集于已知类型集合；未知类型标识符静默忽略。
- **limit 范围**：`ge=1, le=100, default=20`，Pydantic/FastAPI Query 校验。
- **`SearchProvider` 协议**：`@runtime_checkable`，`LikeSearchProvider` 通过鸭子类型满足（无显式继承），`isinstance` 可正常用于测试。

## (b) 测试结果

```
35 passed, 33 warnings in 7.53s
```

`make check` 全通过：
- 后端：`ruff check` ✅、`ruff format --check` ✅、`mypy`（130 个源文件，0 错误）✅、`pytest`（1368 passed）✅
- 前端：`eslint` ✅、`tsc` ✅、`vitest`（467 passed）✅

## (c) 手动验证步骤

1. 启动服务后登录，调用：
   ```
   GET /api/search?q=apple
   ```
   应返回 200，结构含 `item_definitions`、`stock_instances`、`locations`、`categories`、`tags`、`totals` 六个字段。

2. 创建一个带自定义字段 `{"sku": "MY-SKU-XYZ"}` 的 definition，搜索 `MY-SKU-XYZ`，应在 `item_definitions` 中命中。

3. 绑定 barcode `"EAN-9876543210"` 到某 definition，该 definition 下有 instance，搜索 `9876543210`，应在 `stock_instances` 中命中。

4. 搜索 `q=` 空字符串，应返回 200 且所有列表为空、totals 全为 0。

5. 搜索 `?q=test&types=location,category`，`item_definitions`/`stock_instances`/`tags` 应为空。

6. 插入 5 个名称含相同前缀的 location，搜索时带 `limit=2`，`locations` 列表长度 2，`totals.locations` 为 5。

## (d) 偏差说明

无实质性偏差。所有实现均严格遵循 M5.md §4.5（字段列表）、§4.7（API 表面）、§4.8（响应模式）、§9 Step 6（构建范围）。

`SearchService.search()` 与 `SearchProvider` 协议共用相同方法签名，但 `SearchService` 本身不声明为 Protocol 的实现者——这与 `ProductLookupService` 的做法一致。
