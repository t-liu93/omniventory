# M3 —— 保质期/临期 & 易腐品（①）

> 🌐 **语言:** [English](./M3.md) · 中文（当前）

> **里程碑设计文档 —— 自包含。** 请与 `docs/plan/roadmap.md`（地图，尤其是 §5 M3 + 红线 §2.4 *临期信息挂在批次/实例、默认值挂在定义、批次级 best-before*、§2.3 数量由账本派生、§2.6 提醒引擎是 **M4 而非此处**、§2.9 Decimal/Date、§2.11 逻辑在应用层而非数据库）一起阅读；「我们为何存在」见 `docs/inspiration/investigation.md`（Grocy 的批次级 best-before 是我们的标杆）。本文档是 *M3 构建什么、如何验证* 的唯一真相源;不要从 roadmap 重新推导范围。进度**仅**记录在 roadmap §4 表格中。
>
> 已内建约定：原子步骤（§9）、盲审检查点（§10）、🟢 部署自测点（§11），使手动与编排两种执行模式都能挂靠本文档。

---

## 1. 目标与非目标

**目标（roadmap 对 M3 的承诺）：** 给每个批次一个 **best-before（最佳食用/使用日期）**，并让应用*把临期的东西浮现出来*。具体而言：一个按批次的 **`best_before_date`**（批次级精度 —— 红线 §2.4，Grocy 标杆）、一个按定义的 **`default_best_before_days`** 默认保质天数（入库时若未填则**自动算出**批次的 best-before）、**FEFO 消耗**（M2 的 FIFO 走法现在按**最近到期优先**排序 —— 即 M2 §12 的前置承诺：「M3 只改 `ORDER BY`」），以及一个**计算型的临期/过期读取**（`GET /expiring`），以**仪表盘磁贴** + 专门的 **/expiring 页面**呈现，复刻 M2 的低库存范式。

**完成判据（🟢，§11 展开）：** 创建一个易腐定义，`default_best_before_days = 7`;登记一个批次**不**填日期，看到 `best_before_date` 自动算成 *今天 + 7*;再登记第二个批次并显式填一个更近的日期;**消耗**该定义，看到 FEFO **先取最近到期的批次**(而不仅是最早收到的);**仪表盘临期磁贴**列出窗口内将到期的批次并**标记已过期**的;**/expiring 页面**以可调地平线列出同样内容。CI 保持绿，包括无漂移契约门禁。

**非目标（明确排除出 M3 —— 推迟或属于后续里程碑）：**
- **不做提醒/通知。** 临期/过期是一个**计算型读取**（端点 + 仪表盘磁贴 + 页面），与 M2 的低库存完全一样。主动「提前 N 天」的引擎（按物品**与**按用户的提前天数、每日扫描、通道）是 **M4**（roadmap §2.6 / §5 M4）。M3 **不**出任何调度器、无按用户配置、无邮件。
- **不做开封/冷冻/解冻「+N 天」调整。** Grocy 式的易腐状态细化（roadmap §5 M3「选配」、parking lot）**推迟** —— 见 §12。M3 每个批次只有一个 `best_before_date`，无状态机。
- **不加新追踪模式、不做模式切换。** M2 的三种模式（`exact`/`level`/`none`）不变。`best_before_date` 是**与模式无关**的（像 `warranty_expires`）：任何批次都可携带。
- **不做追溯重算。** 编辑定义的 `default_best_before_days` 只影响**未来**入库;已有批次保留其存储的 `best_before_date`（§2）。best-before 是**批次**属性，入库时设定一次，按批次可编辑。
- **不动多单位/横切/多用户。** 单位仍是 M1 的单一字符串;照片/标签/条码是 M5;角色是 M6。此处不碰。
- **不合并保修与临期。** `warranty_expires`（M1，耐用品）与 `best_before_date`（M3，易腐品）保持**独立字段**;在 M4 两者才喂给同一个提醒引擎（roadmap §2.6/§2.7）。M3 不做合并。

---

## 2. 已锁定的决策（M3 planning 中敲定;理据见 roadmap §2/§3 + investigation 第 3 章）

| 方面 | 决策 |
|---|---|
| **best-before 挂在批次上** | 新增可空 **`stock_instances.best_before_date`**（`Date`）。按**批次/批号**，绝不挂在定义上（红线 §2.4 —— 同一物品多到期日的场景正是 def/instance 拆分的存在理由）。**与模式无关**：`exact`/`level`/`none` 批次一律可设（易腐品可用任意方式追踪），与 `warranty_expires` 对称。可空 = 「未追踪到期」（非易腐）。 |
| **保质默认值挂在定义上** | 新增可空 **`item_definitions.default_best_before_days`**（`Integer`，`≥ 0`）。M2 文档刻意「留给其消费者（M3）」—— M3 即该消费者。`NULL` = 无默认保质期。 |
| **入库时自动计算** | 在 **create**（`POST /instances`）时，当 `best_before_date` **被省略** *且* 定义有 `default_best_before_days`，服务设 `best_before_date = today + default_best_before_days`。**优先级：** 显式 `best_before_date` 永远胜（即便是过去日期 —— 你可登记已过期或已知到期日的库存）;省略 + 有默认 → 算出;省略 + 无默认 → `NULL`。参考「今天」是**服务器日期**（`date.today()`）;遵循 `household.timezone` 是已记的细化项（§12）。后续对**已有**批次的 `intake` 流水**不**改其 `best_before_date`（一个批次 = 一个批号 = 一个到期日;不同到期日的收货是**新批次**）。 |
| **FEFO 消耗**（M2 §12 承诺） | `consume` 仍走定义的活跃 `exact` 批次，但 FIFO 排序键以 best-before 为**主**键：**`(best_before_date ASC NULLS LAST, received_at ASC, id ASC)`** —— 最近到期优先，有日期的批次先于永不过期的，`received_at`/`id` 为后备 tie-break。**只改仓储的 `ORDER BY`**（M2 写 `consume_fifo` 时就为此留好,M3 不碰其它）。方法/端点**名称不变**（`consume` / `consume_fifo`）;文档串改为 FEFO。 |
| **NULLS-LAST 显式且可移植** | SQLite 在纯 `ASC` 下把 `NULL` 排**最前**;我们要永不过期的批次排**最后**。排序键以 **`best_before_date IS NULL`**（一个 `0/1` 布尔：非空排在空前）开头，而非依赖方言的 `NULLS LAST` 子句 —— SQLite/Postgres 通用（红线 §2.11 可移植性），逻辑在应用/查询层。 |
| **临期/过期是按批次的计算型读取** | `GET /expiring?within_days=N` 返回 `best_before_date ≤ today + N` 的**批次**（实例）（故集合是 *已过期 ∪ N 天内将到期*），每条带 `status ∈ {expired, expiring}` 与 `days_remaining`（负 = 已过）。粒度是**按批次**（不同于 M2 按**定义**的低库存），因为到期是**批次**属性（红线 §2.4）。纯读取、无持久化 —— M4 *消费*此信号。 |
| **临期列表里什么算「活」库存** | 当且仅当批次有 `best_before_date`、该日期 `≤ today + N`、**且**该批次不是空的 `exact` 批次时纳入 —— 即 `best_before_date IS NOT NULL AND best_before_date ≤ cutoff AND (quantity IS NULL OR quantity > 0)`。`quantity IS NULL` 一支保留 `level`/`none` 批次（在场但未量化）;`> 0` 一支剔除已耗尽的 `exact` 批次，使耗尽的批号不再纠缠列表。 |
| **地平线 `within_days`** | 查询参数，**默认 30**，**钳制 `≥ 0`**。`0` ⇒ 仅已过期 + 今天到期。这是*展示*地平线，**不是**提醒提前天数（那是按物品/按用户的，落在 M4）。 |
| **不加新错误码** | `default_best_before_days ≥ 0` 用 Pydantic 字段约束（`Field(ge=0)`）落实 → 复用既有 `validation.invalid_input` 信封;`best_before_date` 接受任意合法日期（含过去）。`within_days` 是钳制而非拒绝。故 M3 新增 **零** 个 `ErrorCode` —— FE↔BE 错误契约不变。 |
| **不做追溯重算** | 对已有批次的定义改 `default_best_before_days` 绝不重写既有批次的 `best_before_date`。best-before 在批次入库时捕获，只经显式 `PATCH /instances/{id}` 改变。 |

> 这些扩展 M0/M1/M1.5/M2 的地基;它们**不是**「技术栈」变更，故 `AGENTS.md` 对 M3 **不**变。两个新列与 `ExpiringItem` schema 走既有的无漂移契约门禁（§6）与 M1.5 统一错误信封（无新码）。

---

## 3. 数据模型

两处加列（每张既有表各一）+ 临期读取语义。无新表。均用 M0 约定：在共享 `Base` 上用 SQLAlchemy 2.0 typed `Mapped[...]`，业务规则在服务层，DB 访问**仅**经仓储。两列都是**可空的纯加列** —— SQLite 无需 batch 重建（对比 M2 的 `0012`）。

### 3.1 `item_definitions` —— 加列（迁移 `0013`）

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `default_best_before_days` | Integer | 是 | 默认保质天数;`≥ 0`（Pydantic 校验）。`NULL` = 无默认。入库自动计算消费它（§4.2）。编辑**不追溯**（§2）。 |

### 3.2 `stock_instances` —— 加列（迁移 `0014`）

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `best_before_date` | Date | 是 | 按批次/批号的 best-before。`NULL` = 未追踪到期。与模式无关。create 时显式或自动算出（§4.2）;经 `PATCH` 可编辑。FEFO 主排序键（§4.3）与临期读取的过滤条件（§4.4）。 |

> 在形态与生命周期上与既有可空 `warranty_expires`（`Date`）完全对称 —— 存储、序列化（`InstanceResponse`）、`set_*`-flag 更新范式（§4.1）都有干净先例。M3 无 CHECK、无索引（临期扫描像低库存一样是全表读;`best_before_date` 索引是已记的扩容细化项，§12）。

### 3.3 迁移清单

| Rev | 步骤 | 做什么 |
|---|---|---|
| `0013` | 1 | `item_definitions`：加 `default_best_before_days`（可空 Integer）。 |
| `0014` | 2 | `stock_instances`：加 `best_before_date`（可空 Date）。 |

两者下行均以普通 `op.drop_column` 可逆。无数据回填（新可空列默认 `NULL`;既有批次即「未追踪到期」，正确）。

---

## 4. 后端设计

### 4.1 仓储 + 服务分层（扩展 M2）

- **仓储**（`app/repositories/`）：
  - `StockInstanceRepository`：把 `list_active_lots_for_definition` 的 **`ORDER BY` 改**成 FEFO 键（§4.3）—— 该方法*唯一*的改动。新增 `list_expiring(cutoff_date)`（§4.4 过滤;纯数据访问）。`create`/`update` 串接新的 `best_before_date`（`update` 保留 M2 的 `set_*`-flag 范式，使「清空为 NULL」可表达）。
  - `ItemDefinitionRepository`：`create`/`update` 串接 `default_best_before_days`。（无新查询方法。）
- **服务**（`app/services/`）：
  - `ItemDefinitionService`：存/回显 `default_best_before_days`（Pydantic `ge=0` 做校验;无需服务层守卫）。
  - `StockInstanceService`：create 时**自动计算** `best_before_date`（§4.2）;穿过全部三个模式分支;`PATCH` 允许它。**无模式耦合** —— best-before 校验独立于 `exact`/`level`/`none`（不像 `quantity`/`stock_level`）。
  - **`ExpiryService`**（新增、小、纯读 —— M2 `LowStockService` 的兄弟）：`compute(within_days)` → `list[ExpiringItem]`（§4.4）。
- 对 `StockMovementService` **无改动**，除了它从仓储继承的 FEFO 排序（consume 已调 `list_active_lots_for_definition`）。自动计算住在 *create*，不在流水层（best-before 是批次属性，非账本事件）。

### 4.2 入库自动计算（招牌便利）

在 `StockInstanceService.create` 中，**在**模式分支**之前**（best-before 与模式无关），一次性解析有效 best-before：

```
resolve_best_before(data, definition):
    if 'best_before_date' in data.model_fields_set:   # 显式胜，即便过去/None
        return data.best_before_date
    if definition.default_best_before_days is not None:
        return date.today() + timedelta(days=definition.default_best_before_days)
    return None
```

- **显式胜过默认：** 提供的 `best_before_date`（含显式过去日期）原样存;自动计算只填**被省略**的值。
- **参考日期：** `date.today()`（服务器日期;household-tz 细化记于 §12）。在服务里算，无需 flush 后读 DB。
- 然后在每个模式分支（`exact`/`level`/`none`）`repo.create(..., best_before_date=resolved)`。
- `PATCH /instances/{id}` 直接设/清 `best_before_date`（更新时不自动计算 —— 更新是显式修正）。

### 4.3 FEFO 消耗（只改 `ORDER BY`）

`StockInstanceRepository.list_active_lots_for_definition` —— M2 §12 预授权的 FIFO→FEFO 互换：

```python
stmt = (
    select(StockInstance)
    .where(
        StockInstance.definition_id == definition_id,
        StockInstance.quantity > 0,
    )
    .order_by(
        StockInstance.best_before_date.is_(None),  # 0=有日期者先, 1=NULL 最后（可移植 NULLS LAST）
        StockInstance.best_before_date,             # 最近到期优先
        StockInstance.received_at,                  # 再最早收到（M2 tie-break）
        StockInstance.id,                           # 再稳定 id
    )
)
```

`StockMovementService.consume_fifo` **不动** —— 它按仓储返回的顺序走，故现在自动**最近到期优先**消耗（有日期的先于永不过期的;同到期日的最早收到优先）。端点仍是 `POST /definitions/{id}/consume`;文档串改为 FEFO。

### 4.4 临期/过期（计算型读取）

`GET /expiring?within_days=N` → 按批次列表。服务算截止点并把过滤委托给仓储：

```
compute(within_days):
    within_days = max(within_days, 0)           # 钳制，绝不拒绝
    today  = date.today()
    cutoff = today + timedelta(days=within_days)
    lots   = repo.list_expiring(cutoff)          # best_before 非 NULL 且 <= cutoff
                                                 #   且 (quantity IS NULL OR quantity > 0)
                                                 # ORDER BY best_before_date, id
    for lot in lots:
        days = (lot.best_before_date - today).days
        yield ExpiringItem(
            instance_id   = lot.id,
            definition_id = lot.definition_id,
            name          = lot.definition.name,
            location_id   = lot.location_id,
            best_before_date = lot.best_before_date,
            quantity      = lot.quantity,          # Decimal | None（依模式）
            days_remaining = days,                 # 负 = 已过期
            status        = 'expired' if days < 0 else 'expiring',
        )
```

- **排序：** 最快/逾期最久的在前（`ORDER BY best_before_date, id`）—— 最紧急的置顶，已过期天然领先（其日期最早）。
- **`name`** 取自批次的定义;仓储 eager-load 或服务解析（低库存已加载定义 `name`,复用同一访问范式 —— 家庭级小数据集无 N+1,若增长在 §12 有记）。
- 纯读取;**无写**。M4 将消费这一精确信号做主动提醒。

### 4.5 API 面（相对 M2 的增量;均在 `settings.api_prefix`，默认 `/api`;均经 `get_authenticated_context` 认证）

| 方法 + 路径 | 变化 |
|---|---|
| `POST /definitions` · `PATCH /definitions/{id}` | 现也接受 **`default_best_before_days`**（`int ≥ 0`，可空） |
| `POST /instances` | 现接受 **`best_before_date`**;省略且定义有默认时**自动算出**（§4.2） |
| `PATCH /instances/{id}` | **`best_before_date`** 可设/清（可空，`set_*`-flag 范式） |
| `GET /instances/{id}` · `GET /instances` | `InstanceResponse` 现携带 **`best_before_date`** |
| `POST /definitions/{id}/consume` | 形状不变;现为 **FEFO**（最近到期优先）—— §4.3 |
| `GET /expiring?within_days=N` | **新增** —— 计算型 已过期 ∪ N 天内将到期 的批次列表（§4.4）;`within_days` 默认 30，钳制 `≥ 0` |

错误契约（信封不变、无新码）：`default_best_before_days < 0` → `422 validation.invalid_input`;`401` 无会话。`GET /expiring` 除认证外无 4xx。

### 4.6 Pydantic schema（`app/schemas/`）

- `DefinitionCreate/Update/Response` 增 `default_best_before_days: int | None`（create/update 用 `Field(default=None, ge=0)`;`Update` 仍可选可空）。
- `InstanceCreate/Update/Response` 增 `best_before_date: date | None`。`Update` 用既有可选可空约定，使服务的 `model_fields_set` 检查能区分「省略」与「清为 NULL」。
- 新增 `ExpiringItem`（§4.4 形状）：`instance_id`、`definition_id`、`name`、`location_id: int | None`、`best_before_date: date`、`quantity: Decimal | None`、`days_remaining: int`、`status: str`（`"expired"`/`"expiring"`）。quantity 用 `Decimal`（红线 §2.9）;日期用 `date`。

---

## 5. 质量门禁与易出错逻辑（Definition of Done）

继承 M0–M2 §5（`make check` = ruff + mypy + pytest + eslint + tsc + vitest 全绿;build 通过;`make codegen` 无漂移）。M3 **必须有单测的逻辑**（roadmap DoD —— *临期/提前天数日期计算*、*消耗顺序*）：

**后端**
- **自动计算优先级：** 省略 `best_before_date` + 定义默认 `N` ⇒ `best_before_date == today + N`;**显式日期胜**（含显式过去日期，以及显式 `None` 保持 `NULL`）;省略 + **无**默认 ⇒ `NULL`;**三种模式**（`exact`/`level`/`none`）皆然。不追溯：改定义默认不改既有批次。
- **FEFO 排序（消耗顺序 —— 必测）：** 两个批次，**最近到期**的先消耗**即便它收到更晚**（证明到期胜过 `received_at`）;**NULL**-best-before 批次**最后**消耗（NULLS-LAST）;**同**到期日回退到 `received_at` 再 `id`;多批次跨取与分批 `Decimal` 精度仍成立（M2 不变量不破）;总量不足仍拒绝且不写任何东西。
- **临期读取：** `best_before < today` ⇒ `status='expired'`、`days_remaining < 0`;`today ≤ best_before ≤ today+N` ⇒ `status='expiring'`、`days_remaining ≥ 0`;**边界** `best_before == today+N` 纳入、`today+N+1` 排除;`within_days=0` ⇒ 仅 已过期+今天;负 `within_days` **钳制**为 0;耗尽的 `exact` 批次（`quantity=0`）**排除**;有日期的 `level`/`none` 批次（`quantity=NULL`）**纳入**;排序最快优先;无符合时空结果。
- **schema 约束：** `default_best_before_days < 0` ⇒ 422 `validation.invalid_input`;`0` 接受（当天到期）。
- **迁移往返：** `0013`、`0014` 在 `0012` 的库上干净上行并干净下行（纯加/删列）;既有行两新列得 `NULL` 且显示数据不变。

**前端**（vitest + Testing Library，mock typed client —— M0 风格）：定义表单展示/存 `default_best_before_days`;实例表单有 `best_before_date` 选择器且（可选）在有默认时显示将算出的提示;实例/批次详情按 `best_before_date` 渲染**到期徽章**（已过期=红、将到期=琥珀、否则无）;**仪表盘临期磁贴**从 `GET /expiring` 渲染计数 + 短列表带空态;**/expiring 页面**渲染列表与地平线控件;日期经 `formatDate`（M1.5 locale-aware）。

---

## 6. 契约优先 codegen 与无漂移门禁

机制不变（M0 §6 / M1 §6 / M2 §6）：触 API 的步骤**重跑 `make codegen`**（重生成仓根 `openapi.json` + `frontend/src/api/schema.d.ts`）并提交两者;CI **contract** job 在漂移时失败。M3 用两新字段（`default_best_before_days`、`best_before_date`）、新 `ExpiringItem` 模型、`GET /expiring` 路径扩展 schema。**步骤 3（FEFO `ORDER BY`）不触 schema** —— 那里无需 codegen;其余每个后端步骤都需。

---

## 7. 前端设计

### 7.1 定义表单与详情（`pages/Items.tsx`）
- `DefinitionFormModal` 增 **`default_best_before_days`** `NumberInput`（可选，min 0，后缀「天」）—— 三种模式都显示（易腐品可用任意模式）。定义详情卡在设值时显示「默认保质期：N 天」。

### 7.2 实例表单（`components/InstanceFormModal.tsx`）
- 增 **`best_before_date`** `DatePickerInput`（可选），置于既有 `warranty_expires` 旁（同形态/生命周期）。当该字段为空**且**父定义有 `default_best_before_days` 时，显示非阻断提示（「留空将默认为 <today + N>」）和/或预填算出的日期 —— 预填更简单清晰;用户可改或清。M2 的模式分支不动（best-before 与模式无关）。

### 7.3 批次上的到期徽章（`InstanceDetail` + `ItemDetail`）
- 一个小 **`ExpiryBadge`** 助手（纯前端、表现层）：给定 `best_before_date`，过去时渲染**红「已过期」**，临近时（前端展示常量，如 30 天）**琥珀「N 天后到期」**，远期或缺失时无。在 `InstanceDetail`（批次）与 `ItemDetail` 批次表每行显示，紧邻 `formatDate(best_before_date)`。此徽章**仅展示**（**不**重实现服务端临期列表 —— 那在 `GET /expiring` 后面）;它是本地便利提示。

### 7.4 仪表盘临期磁贴（`pages/Dashboard.tsx`）
- 既有**静态** `expiryCard` 占位（卡 1）变为**活的 `ExpiryCard`** —— 挂载时拉一次 `GET /expiring`，显示计数徽章 + 短列表（定义名 · `formatDate(best_before)` · 已过期/将到期提示）、无符合时**空态**、以及到 **/expiring** 的链接。结构上复刻 `LowStockCard`（loading/error/empty/list 状态、`data-testid`）。绝不在客户端重派生规则。

### 7.5 /expiring 页面（`pages/Expiring.tsx`，新增）+ 路由
- 一个复刻 `pages/LowStock.tsx` 的专门页面：完整 `GET /expiring` 列表，每行链到 `/instances/:id`，分组/排序使**已过期**领先、再**将到期**最快优先;一个小**地平线控件**（如 7 / 30 / 90 天）重查 `within_days`。在 `App.tsx` 注册 `<Route path="/expiring" element={<Expiring />} />`（与 `/low-stock` 并列）。可选加导航项（与 `/low-stock` 的呈现一致）。

### 7.6 i18n
- 在 **`en` 与 `zh`** 都加新串（M1.5 规则）：新 **`expiry`** 命名空间（best-before 标签、「已过期」/「{{count}} 天后到期」、保质期、磁贴/页面文案、地平线控件），在 `src/i18n/index.ts` 注册;另加 `items`（`default_best_before_days`）、`instances`（`best_before_date`）、`dashboard`（临期磁贴）。**无新 `errors` 键**（无新码）。测试仍钉在 `en`（M1.5）。

### 7.7 测试（vitest + Testing Library）
按 §5「前端」：定义默认保质天数字段;实例 best-before 选择器 + 计算提示/预填;`ExpiryBadge` 按日期 红/琥珀/无;仪表盘临期磁贴（计数/列表/空）;/expiring 页面列表 + 地平线控件。保持 M0「mock typed client」风格。

---

## 8. CI 与 Docker（相对 M2 的增量）

无结构变化。**docker** 冒烟测试的迁移步现在对全新 bind-mount 库应用 `0001`–`0014`（原 `0001`–`0012`）。其余（缓存、契约门禁、单镜像、fail-closed `migrate` 服务）与 M0 §8 / M2 §8 完全一致。

---

## 9. 步骤拆分（原子、有序）

每步可独立测试、有测试背书、落恰好一个 commit（编排模式下每步一次 autosquash），触 API 则重跑 `make codegen`，并继承全局 DoD（§5）+ **反夹带规则**：只实现*本步* —— 不做别的步骤、不顺手重构、不碰宿主真实环境。

> **排序理据：** 定义默认（`0013`）与实例列（`0014`）是相互独立的加列;实例列须先于写它的自动计算存在，故 `0014` + 自动计算同落（步骤 2）。FEFO（步骤 3）在 `best_before_date` 存在后只重排既有 consume 查询。临期读取（步骤 4）坐在同一列上。前端（5–6）消费后端落地的 typed 契约。

### 步骤 1 —— 定义：`default_best_before_days`（后端）
- **目标：** 按定义的保质默认值，校验并上契约。
- **构建：** 迁移 `0013`（加可空 Integer）;扩展 `ItemDefinition` 模型 + `ItemDefinitionRepository` create/update;`DefinitionCreate/Update/Response` 增 `default_best_before_days`（`Field(ge=0)`，可空）;`ItemDefinitionService` 存/回显;`make codegen`。
- **测试：** 存/回显;默认 `None`;`< 0` 拒（422 `validation.invalid_input`）;`0` 接受;此处不追溯无意义（还无实例列）;迁移 `0013` 上/下行。
- **超范围：** 实例列、自动计算（步骤 2）;FEFO/临期（3/4）。
- **Commit：** `feat(backend): per-definition default best-before days`

### 步骤 2 —— 实例：`best_before_date` + 自动计算（后端）
- **目标：** 按批次的 best-before 列，create 时显式或自动，update 时可编辑。
- **构建：** 迁移 `0014`（加可空 Date）;扩展 `StockInstance` 模型 + `StockInstanceRepository` create/update（`set_best_before_date` flag）;`Instance{Create,Update,Response}` 增 `best_before_date`;`StockInstanceService.create` 解析 best-before（显式胜 → 定义默认 → NULL —— §4.2）并穿过三模式分支;`PATCH` 设/清;`make codegen`。
- **测试（易出错的日期数学 —— 必测，按 §5）：** 自动算 `today + N`;显式日期（含过去）胜;显式 `None` 保持 NULL;省略 + 无默认 → NULL;三种模式;不追溯（改定义默认后既有批次日期不变）;PATCH 设/清;迁移 `0014` 上/下行。
- **超范围：** FEFO（步骤 3）;临期端点（步骤 4）;前端。
- **Commit：** `feat(backend): per-lot best-before date with intake auto-compute`

### 步骤 3 —— FEFO 消耗排序（后端）
- **目标：** 最近到期优先消耗（M2 §12 承诺）。
- **构建：** **只**改 `StockInstanceRepository.list_active_lots_for_definition` 的 `ORDER BY` 为 `(best_before_date IS NULL, best_before_date, received_at, id)`（§4.3）;把该方法 + `consume_fifo` 的文档串改为 FEFO。**无 schema 变化 → 无 codegen。**
- **测试：** 最近到期先消耗即便收到更晚;NULL-best-before 批次最后;同到期日按 `received_at` 再 `id` tie-break;M2 不变量完好（多批次跨取、分批 `Decimal`、不足时拒绝且不写）。
- **超范围：** 临期读取（步骤 4）;任何流水 API 形状变化。
- **Commit：** `feat(backend): FEFO consumption ordering by best-before`

### 步骤 4 —— 临期/过期计算型端点（后端）
- **目标：** `GET /expiring` 信号（已过期 ∪ N 天内将到期）。
- **构建：** `ExpiryService.compute(within_days)`（§4.4）+ `StockInstanceRepository.list_expiring(cutoff)`;`ExpiringItem` schema;`GET /expiring?within_days=N` 路由（默认 30，钳 `≥ 0`）;`make codegen`。
- **测试：** 已过期/将到期/边界（`==today+N` 入、`+1` 出）;`within_days=0`;负值钳制;耗尽 `exact` 批次排除;有日期的 `level`/`none` 批次纳入;排序最快优先;空结果。
- **超范围：** 前端磁贴/页面（步骤 6）。
- **Commit：** `feat(backend): computed expiring/expired endpoint`

### 步骤 5 —— 前端：表单中的 best-before + 批次到期徽章
- **目标：** 在 UI 录入 best-before 并在批次上提示到期。
- **构建：** `DefinitionFormModal` `default_best_before_days` NumberInput + 详情展示;`InstanceFormModal` `best_before_date` DatePickerInput + 计算默认提示/预填;`ExpiryBadge` 助手用于 `InstanceDetail` + `ItemDetail` 批次行;新 `expiry` 命名空间 + `items`/`instances` 串（en+zh）在 `src/i18n/index.ts` 注册。
- **测试：** 默认保质天数字段;best-before 选择器 + 提示/预填;`ExpiryBadge` 按日期 红/琥珀/无。
- **超范围：** 仪表盘磁贴 + /expiring 页面（步骤 6）。
- **Commit：** `feat(frontend): best-before fields and lot expiry badges`

### 步骤 6 —— 前端：仪表盘临期磁贴 + /expiring 页面
- **目标：** 在仪表盘与专门视图浮现临期信号。
- **构建：** `Dashboard.tsx` 静态 `expiryCard` → 活 `ExpiryCard`（从 `GET /expiring` 取计数 + 短列表 + 空态，链到 /expiring）;新 `pages/Expiring.tsx`（完整列表，行链到 `/instances/:id`，已过期优先排序，地平线控件重查 `within_days`）;`App.tsx` 加 `<Route path="/expiring">`（+ 可选导航项）;`dashboard`/`expiry` 串（en+zh）。
- **测试：** 临期磁贴（计数/列表/空/链接）;/expiring 页面列表 + 地平线控件;导航。
- **超范围：** 消费此信号的 M4 提醒引擎。
- **Commit：** `feat(frontend): dashboard expiry tile and expiring view`

> 步骤 5/6 若过大可再拆（定义 vs 实例;磁贴 vs 页面）;各自保持独立绿。

---

## 10. 盲审检查点（每步）

审查者**仅**拿到：本文档 + roadmap、该步实现简报、该步 diff。检查：

- **步骤 1：** `default_best_before_days` 是**可空 Integer**，用 **Pydantic `ge=0`** 校验（无 DB CHECK、无新错误码）;默认 `None`;迁移可逆;codegen 已提交。
- **步骤 2：** best-before 是**按批次**（列在 `stock_instances`，非定义）;自动计算优先级为**显式胜 → 定义默认 → NULL**，参考 `date.today()`，在**服务**算（非 SQL）;穿过**全部三个**模式分支;**与模式无关**（无 `field_mode_mismatch` 耦合）;对已有批次的后续 intake **不**改其日期;对既有批次**不追溯**;`PATCH` 可经 `set_*` flag 清为 NULL;codegen 已提交。
- **步骤 3：** **只**改了 `ORDER BY`;NULLS-LAST 键以 `best_before_date IS NULL` 开头（可移植，不依赖方言 `NULLS LAST`）;`consume_fifo` 主体未动;M2 不变量（不足拒绝且不写、`Decimal` 精度）完好;**无 schema/codegen 漂移**。
- **步骤 4：** 过滤是 `best_before NOT NULL AND ≤ cutoff AND (quantity IS NULL OR quantity > 0)`;`status`/`days_remaining` 对 `date.today()` 计算;`within_days` **钳制**而非拒绝;**按批次**粒度;纯读（无写）;排序最快优先;codegen 已提交。
- **步骤 5：** `default_best_before_days` NumberInput min 0;best-before 选择器用 typed client;`ExpiryBadge` **仅展示**（不重派生服务端列表）;`en`+`zh` 两份目录都更新（无缺键）;日期经 `formatDate`。
- **步骤 6：** 磁贴/页面读 `GET /expiring`（不在客户端重派生规则）;空态已处理;地平线控件重查 `within_days`;路由已注册;en+zh 完整。
- **横切：** 契合 roadmap §2（尤其 **§2.4 临期挂批次/批次级/默认挂定义**、§2.9 Decimal/Date、§2.11 逻辑在应用层而非数据库、**§2.6 提醒是 M4 —— 此处无**）、M1.5 统一错误信封（无新码、无裸 `detail`）、以及本文档变更时的**双语文档规则**。

---

## 11. 🟢 部署自测点（串入 M3 里程碑 walkthrough）

里程碑末作者跑的人工 walkthrough（展开 roadmap M3 🟢 条目）。假定 M1/M2 的运行流程（compose up;以 admin 登录;已有位置树 + 一个分类）：

1. **带默认保质期的易腐定义：** 创建定义 `Milk`（模式 = exact，单位 = `bottle`，**`default_best_before_days = 7`**）。定义详情显示「默认保质期：7 天」。
2. **入库自动计算：** 登记一个 `Milk` 批次，初始数量 **2**，**不**填 best-before → 该批次 `best_before_date` 为 **今天 + 7**（批次详情可见）。再登记第二个批次并**显式填**一个更近的日期（如 今天 + 2）→ 原样存。
3. **显式胜过默认 / 允许过去日期：** 登记第三个批次，显式填一个**过去**日期 → 原样存并显示**「已过期」**徽章。
4. **FEFO 消耗（最近到期优先）：** **消耗 2** 个 `Milk` → **今天+2** 的批次在 今天+7 之前被取空，即便 今天+7 可能收到更早（账本显示 consume 先打到更近到期的批次）。
5. **仪表盘临期磁贴：** 仪表盘**临期磁贴**显示计数并列出窗口内批次，**已过期**那个被标记;只有远期/空集时显示空态。
6. **/expiring 页面 + 地平线：** 打开 **/expiring** → 列表先已过期再最快将到期;切换地平线（7 / 30 / 90）列表重查;每行链到其批次详情。
7. **默认值不追溯：** 把 `Milk` 的 `default_best_before_days` 改为 14 → **既有**批次保留其日期;一个不填日期的**新**批次现算 今天 + 14。
8. **带日期的 level/none 批次：** 登记一个 `level`- 或 `none`-模式批次（任意易腐品），显式填一个过去 best-before → 它出现在临期列表（基于在场;无需数量）并被标记已过期。
9. **CI 绿：** 同样门禁在 GitHub Actions 通过，含无漂移契约门禁;迁移 `0001`–`0014` 在 docker 冒烟测试中干净应用到全新库。

---

## 12. 待定/推迟

- **开封/冷冻/解冻「+N 天」（Grocy 式）：** 推迟（roadmap §5 M3「选配」+ parking lot）。将给批次加一个易腐状态 + 「开封 +N 天 / 冷冻 +M 天」对 `best_before_date` 的调整。待真实使用显示单日期模型太粗再回看。
- **「今天」的时区：** M3 用**服务器** `date.today()`。单例 `Household` 已带 `timezone`;为到期边界遵循它（使「今天到期」在家庭午夜翻转，而非服务器午夜）是干净的细化 —— 与 M4 的定时扫描配套（后者也需同一「现在」概念）。
- **best-before 索引：** 临期扫描是全表读（家庭级够用）。若数据集增长，给 `stock_instances.best_before_date` 加索引（可能为 FEFO 键加复合索引）。记下，未做。
- **`within_days` 默认 / 按用户地平线：** M3 把展示默认钉在 30（钳 `≥ 0`）并给地平线控件。真正**可配置、按物品*与*按用户的提前天数**是 **M4** 的事（roadmap §2.6）—— M3 的地平线是展示过滤器，非提醒策略。
- **临期读取里的定义 `name` 解析：** M3 内联解析批次的定义名（小数据集）。若列表变大，批量加载定义以避免 N+1 —— 与任何列表端点同理。
- **保修 ↔ best-before 统一：** 保持**独立**（M1 耐用 vs M3 易腐）。**M4** 才把两者（加低库存，以及之后的维护到期）喂给**一个**提醒引擎;M3 刻意不合并。
- **入库暴露 `received_at` / 回溯日期的自动计算：** M3 从 `date.today()` 自动算，回溯易腐品的用户直接手填已知日期（显式胜）。把 `received_at` 接进 `POST /instances` 使自动计算能参考回溯收货日（即 M2「received_at 粒度」遗留项）推迟 —— 若手填显出摩擦再回看。
