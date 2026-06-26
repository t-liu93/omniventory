# M5 Step 7 — 实现简报：CSV / JSON 数据导出

## (a) 实现内容

### 新增文件

**`backend/app/services/export.py`** — `ExportService`

- `export(entity, fmt) -> Iterator[str]`：验证参数，获取记录（立即执行），返回 CSV 或 JSON 生成器（惰性编码）。
  - 坏 `entity` 或 `fmt` 立即抛出 `AppError(validation.invalid_input, 422)`，错误在 `StreamingResponse` 创建前传播。
- 三个实体各有独立的记录构建方法，每个方法采用**批量查询**策略：
  - FK 表（categories、locations、item_definitions）各执行一次 SELECT，构建 `{id: name}` 字典；
  - tag_links + tags 各执行一次 SELECT，构建 `{model_id: [tag_name, ...]}` 字典；
  - 无 N+1 查询。
- **CSV**：每行通过 `_csv_encode_row()` 调用 stdlib `csv.writer` + `io.StringIO` 编码，正确处理逗号、双引号、换行符。
- **JSON**：生成器逐块 yield `"["` → 对象块 → `"]\n"`，不在内存中构建完整字符串。

#### 各实体列定义（CSV 表头 = JSON 键）

**item_definitions**（16 列）：
`id`, `name`, `description`, `kind_id`, `unit`, `category_id`, `category_name`, `default_location_id`, `default_location_name`, `stock_tracking_mode`, `min_stock`, `default_best_before_days`, `reminder_lead_days`, `custom_fields`, `tags`, `created_at`

**stock_instances**（20 列）：
`id`, `definition_id`, `definition_name`, `location_id`, `location_name`, `quantity`, `stock_level`, `received_at`, `serial`, `model_number`, `manufacturer`, `warranty_expires`, `warranty_details`, `best_before_date`, `purchase_price`, `purchase_date`, `purchase_source`, `custom_fields`, `tags`, `created_at`

**locations**（8 列）：
`id`, `name`, `description`, `parent_id`, `parent_name`, `item_instance_id`, `tags`, `created_at`

#### 值格式化

- `Decimal` → `str(v)`（无区域格式，如 `"1234.99"`）
- `date` → `v.isoformat()`（如 `"2027-12-31"`）
- `datetime` → `v.isoformat()`（如 `"2026-06-27T10:30:00+00:00"`；`datetime` 先于 `date` 检查，因为 datetime 是 date 的子类）
- `None` → CSV 空单元格 `""`；JSON `null`
- CSV 中 `custom_fields` = 原始 JSON 字符串（或空）；JSON 中同样为字符串，保持格式一致

**`backend/app/api/routes/export.py`** — 路由

- `GET /export/{entity}?format=csv|json`（session 认证）
- 使用 `Query(alias="format")` 避免遮蔽 Python 内置 `format`，Python 参数命名为 `fmt`
- 返回 `StreamingResponse`，media type 为 `text/csv` 或 `application/json`
- `Content-Disposition: attachment; filename="<entity>-<YYYY-MM-DD>.csv|json"`
- `response_class=StreamingResponse` — OpenAPI 文档标注为文件下载，无 JSON 响应 schema

**`backend/app/main.py`** — 路由注册

在 import 块按字母顺序（`expiry` 之后，`instances` 之前）添加 `export_router`，并在 `root_router` 中注册。

**`openapi.json` + `frontend/src/api/schema.d.ts`** — `make codegen` 重新生成并提交。

**`backend/tests/test_m5_step7.py`** — 47 个测试用例（见下文）

---

## (b) 自动化测试结果

```
Backend pytest:  1415 passed, 0 failed  (含本步骤 47 个新测试)
Frontend vitest: 467  passed, 0 failed
Backend ruff:    All checks passed
Backend mypy:    Success: no issues found in 132 source files
Frontend eslint: 无报错
Frontend tsc:    无报错
make check:      全绿
```

**Step 7 测试覆盖的场景（47 个）：**

| 测试类 | 覆盖点 |
|---|---|
| `TestCSVHeaders` | 三实体表头行与列定义完全匹配；空数据库输出仅表头 |
| `TestCSVDataRows` | 三实体数据行字段值正确；FK id+name 解析正确 |
| `TestFKResolution` | NULL FK → CSV 空单元格；JSON null |
| `TestTagsColumn` | 单标签、多标签逗号连接、无标签；三实体各一 |
| `TestCustomFieldsColumn` | definition/instance custom_fields JSON 字符串；NULL → 空；JSON 导出为字符串类型 |
| `TestCSVEscaping` | 逗号、双引号、换行符、三者组合的 CSV 往返校验 |
| `TestJSONShape` | JSON 键与 CSV 列名一致；值等价；空数据库输出 `[]` |
| `TestValidationErrors` | 坏 entity → 422；坏 format → 422；两者都坏 → 422 |
| `TestUnauthenticated` | 未登录 → 401 |
| `TestStreaming` | `export()` 返回生成器（非实例化列表）；响应 Content-Type 正确 |
| `TestDecimalAndDateFormatting` | Decimal 无区域分隔符；日期 ISO 8601；JSON 中 Decimal 为字符串 |
| `TestContentDisposition` | 两种格式的 attachment + filename 含当天日期 |
| `TestAllEntitiesAndFormats` | 3×2 参数化冒烟测试，全部返回 200 |

---

## (c) 手动验证步骤

1. 启动服务：`docker compose up -d`（或 dev 模式）
2. 登录后访问 `GET /api/export/item_definitions?format=csv` → 浏览器下载 `item_definitions-<today>.csv`，用 Excel/LibreOffice 打开，确认表头和数据正确，FK 字段同时显示 id 和名称列。
3. 访问 `GET /api/export/stock_instances?format=json` → 下载 JSON 文件，确认是 JSON 数组，Decimal 字段（如 `purchase_price`）为字符串，日期字段为 ISO 格式字符串。
4. 访问 `GET /api/export/locations?format=csv` → 确认 `parent_id`/`parent_name` 列有值（若有父子结构）；根节点两列均为空。
5. 在有标签和 custom_fields 的实体上导出 → `tags` 列逗号连接，`custom_fields` 列为 JSON 字符串。
6. 访问 `GET /api/export/bad_entity?format=csv` → 返回 422 JSON，`code="validation.invalid_input"`。
7. 未登录访问 → 返回 401。

---

## (d) 偏差说明

- **`kind_id` 无 name 解析**：设计文档 §4.6 FK 列表仅明确提到 `category_id/category_name`、`location_id/location_name`、`definition_id/definition_name`、`parent_id/parent_name`，未提及 `kind_id`。为严格遵循设计文档，`kind_id` 仅导出 ID，不添加 `kind_name` 列。
- **`custom_fields` 在 JSON 中为字符串**：设计文档要求"same flattened records"且"custom_fields as a JSON string column"，两种格式保持一致（均为字符串），便于格式间互操作。
- **记录急迫获取 + 惰性编码**：记录列表在 `export()` 中急迫构建（对家庭规模数据完全合理），编码通过生成器惰性进行。这满足"流式传输"要求（HTTP 响应体逐行发送），同时保持错误处理简单直接。
