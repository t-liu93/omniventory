# M2 — 库存账本与消耗品（③）

> 🌐 **语言:** [English](./M2.md) · 中文（当前）

> **里程碑设计文档 — 自包含。** 请与 `docs/plan/roadmap.md`（地图，尤其是 §5 M2 + 红线 §2.3 数量由账本派生、§2.9 Decimal、§2.11 逻辑在应用层而非数据库）一起阅读；「我们为何存在」见 `docs/inspiration/investigation.md`。本文档是 *M2 构建什么、如何验证* 的唯一真相源；不要从 roadmap 重新推导范围。进度**仅**记录在 roadmap §4 表格中。
>
> 已内建约定：原子步骤（§9）、盲审检查点（§10）、🟢 部署自测点（§11），使手动与编排两种执行模式都能挂靠本文档。

---

## 1. 目标与非目标

**目标（roadmap 对 M2 的承诺）：** 把 M1 的静态实例登记册变成一本**活的库存账本**。对批次数量的每一次改动都成为一笔**有类型的、仅追加的移动**；批次的数量由该账本**派生**、绝不被盲目覆盖（roadmap §2.3 — M2 存在就是为了守住的红线）。在账本之上，M2 交付 **FIFO 消耗**、**撤销/冲正**、每个定义的 **`min_stock`** 及**低库存**信号，以及 M1 §12 前瞻需求要求我们在此设计的**三种每定义库存跟踪模式**（`exact` / `level` / `none`）。

**完成判据（🟢，§11 展开）：** 入库 10 个消耗品再消耗 3 个（FIFO），派生数量变为 7；撤销该消耗，看它恢复到 10；在定义上设置 `min_stock`，把库存降到其下，看低库存在仪表盘上浮现；登记一个 `level` 模式的物品并把它切到「低」；登记一个完全没有数量的 `none` 模式物品。CI 保持绿色，包括无漂移契约门禁。

**非目标（明确排除在 M2 之外 — 推迟或属于后续里程碑）：**
- **无保质期/到期。** `best_before_date` 与 `default_best_before_days` 属于 **M3**。M2 的 FIFO 仅按 `received_at` 排序；M3 把 `best_before` 前置为首要 FIFO 键（§4.3）。
- **无提醒。** 低库存在 M2 中是一个**计算读取**（一个端点 + 一个仪表盘磁贴）；*消费*该低库存信号、做主动「提前 N 天」通知的引擎是 **M4**。
- **无多单位换算。** 移动使用定义的单一 `unit` 字符串（M1）。整箱买、按个用是 **M8**。
- **无部分拆分移动。** `move` 仅整批迁移（§2）。把批次的部分数量拆到目标库位新建批次，推迟（§12）。
- **无横切能力。** 照片/附件、标签、备注、自定义字段、条码属于 **M5**；移动仅带一个朴素的 `note` 字符串。
- **无软删除/归档。** 删除批次会**级联删除其移动**（§3.3）。保留审计的软删除推迟（沿用 M1 §12）。
- **无 M0 之外的多用户/角色。** 移动记录操作者 `user_id`（今日仍为单一管理员）作为 M6 的脊梁；尚无角色校验。
- **无定性等级账本。** `level` 与 `none` 模式刻意**没有移动账本**（M1 §12）；只有 `exact` 模式有。

---

## 2. 锁定决策（M2 规划期敲定；理据见 roadmap §2/§3 + investigation 第 3 章）

| 领域 | 决策 |
|---|---|
| **库存跟踪模式**（M1 §12） | 每定义一个 **`stock_tracking_mode`**，**三种模式在 M2 全部实现**：`exact`（Decimal 数量、由账本派生）、`level`（定性 `high`/`medium`/`low`，手动设置，**无账本**）、`none`（仅在册，**无数量、无账本**）。以**应用层校验的 String** 存储（对照 `STOCK_TRACKING_MODES`，无 DB CHECK — 集合可能增长；roadmap §2.11），默认 **`exact`**。（代码会*基于*该值分支，因此 — 不同于 M1 的 `kind`，那是个不含逻辑的 UI 提示、故用 FK 查找表 — 模式是一个封闭的应用层常量集。） |
| **数量由账本派生（红线）** | 对 `exact` 批次，`stock_instances.quantity` 是一个**反规范化缓存**；**`stock_movements` 账本是唯一真相源**。每笔移动后，服务在**同一事务内重算** `quantity = SUM(quantity_delta)` — **绝不**做 `quantity += delta`（不盲目覆盖，roadmap §2.3）。单元测试钉死不变式 `quantity == SUM(deltas)`。相较「读取时实时 SUM」选此方案，以保持列表读取廉价并沿用 M1 的 API 形状；可移植性得以保留，因为重算在**应用层、非 SQL 触发器**（§2.11）。 |
| **仅追加账本** | 移动是**不可变的**：从不编辑、从不删除（除随批次级联）。错误通过**追加**一笔冲正移动修复，而非改动历史。 |
| **撤销 = 冲正条目** | 「撤销」追加一笔**冲正移动**，`quantity_delta = −原值`、`type = correction`、`reverses_movement_id → 原移动`。一笔移动**至多被冲正一次**（`reverses_movement_id` 上的部分唯一索引），**冲正本身不可再被冲正**，且会把批次驱为**负数的冲正被拒绝**（§4.4）。 |
| **FIFO 消耗** | `consume` 是**定义级**操作：它按 `(received_at, id)` **从最旧开始**遍历该定义的 `exact` 批次（M3 前置 `best_before`），逐个用 `consume` 移动扣减，直到满足请求数量。**总库存不足时拒绝**（422）— M2 绝不让库存变负。 |
| **移动语义** | **仅整批。** `move` 改变批次的 `location_id` 并记录一笔 `move` 移动（`delta = 0`，`from_location_id` → `to_location_id`）。部分拆分移动推迟（§12）。 |
| **`min_stock` 与低库存** | `min_stock` 是**定义**上的可空 `Numeric`（默认值落在定义上，roadmap §2.4），**仅对 `exact` 模式有意义**。低库存是**计算出来的**：`exact` → 设置了 `min_stock` 时 `SUM(批次数量) < min_stock`（严格小于；`<` vs `≤` 见 §12）；`level` → **任一**批次处于 `low`；`none` → 永不。 |
| **`received_at`** | 新增 `stock_instances.received_at`（`DateTime(tz)`，默认 `now()`，入库时**可回填**）— FIFO 排序键 + roadmap 脊梁 §3 的实物接收时间戳。与 `created_at`（行创建）区分，使回填的入库能正确排序。 |
| **`quantity` 变为可空** | `exact` 批次始终携带数值数量（服务管理）；`level`/`none` 批次携带 **`NULL`**（信号是 `stock_level`，或没有）。M1 的 serial CHECK 改写为 `serial IS NULL OR quantity IS NULL OR quantity = 1`（batch 模式迁移）。 |
| **创建 vs 移动** | `POST /instances` 仍接受初始 `quantity`；对 `exact` 模式，服务会**记录一笔等额的初始 `intake` 移动**（连登记也走账本 — 这也是 M1 §12 对既有行回填所做的事）。`quantity` 从 **`InstanceUpdate` 中移除**：一旦创建，`exact` 批次的数量**仅**通过移动端点变化。`stock_level` 加入 `level` 模式的创建/更新。 |
| **移动操作者** | 每笔移动存储 `user_id`（来自 `RequestContext` 的操作用户，可空 FK → `users`）— M6 的审计脊梁。今日恒为单一管理员；回填置 `NULL`（系统）。 |
| **API 形状** | **显式、意图清晰的操作端点**（`/intake`、`/move`、`/adjust`、`/discard`、`/consume`、`/reverse`），而非一个被重载的通用 movement POST — 每个有不同的必填字段集，使有类型契约自说明、客户端代码保持简单。 |
| **空批次保留** | 被消耗到 `0` 的批次**保留**（历史 + 身份），FIFO 直接跳过 `quantity = 0` 的批次。不自动删除。 |

> 这些扩展 M0/M1/M1.5 的根基；它们**不是**「技术栈」变更，故 `AGENTS.md` 不因 M2 改动。账本 schema 与新错误码经由既有的无漂移契约门禁（§6）与 M1.5 统一错误信封（§4.7）流转。

---

## 3. 数据模型

三处 schema 增量（两张既有表改动、一张新表）+ 模式语义。均遵循 M0 约定：共享 `Base` 上的 SQLAlchemy 2.0 类型化 `Mapped[...]`、`created_at` 经 `server_default=func.now()`、**仅**经仓储访问、业务规则在服务层。

### 3.1 `item_definitions` — 新增（迁移 `0010`）

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `stock_tracking_mode` | String(16) | 否 | `server_default='exact'`；应用层对照 `STOCK_TRACKING_MODES = ("exact","level","none")` 校验。无 DB CHECK（roadmap §2.11）。 |
| `min_stock` | Numeric(18,6) | 是 | 再订货点；**仅对 `exact` 模式有意义**。`NULL` = 无低库存阈值。 |

> `default_best_before_days` 仍**缺席**（由其消费者 M3 添加 — M1 §2「定义默认值时机」）。两者均用普通 `op.add_column` 即可（可空/带 server_default 的列在 SQLite 上无需 batch 重建）。

### 3.2 `stock_instances` — 改动（迁移 `0012`）

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `quantity` | Numeric(18,6) | **是**（原为否） | `exact` 批次：账本派生缓存。`level`/`none`：`NULL`。 |
| `stock_level` | String(16) | 是 | **新增**；仅 `level` 模式：`high`/`medium`/`low`，应用层对照 `STOCK_LEVELS` 校验。`exact`/`none` 为 `NULL`。 |
| `received_at` | DateTime(tz) | 是 | **新增**；`server_default=func.now()`；FIFO 键 + 实物接收时间。入库可回填。（设为可空以便干净加列；服务对 `exact` 批次总会设置它。） |

- **CHECK 改写**（batch 模式 — SQLite 无法原地 `ALTER`/重设 `CHECK`）：`ck_stock_instances_serial_qty_1` 变为 **`serial IS NULL OR quantity IS NULL OR quantity = 1`**。部分唯一索引 `(definition_id, serial) WHERE serial IS NOT NULL` 不变。
- serial 约束对 `exact` 批次**仍在服务中强制**：任何会把*带序列号*批次重算后数量推离 `1` 的移动（尤其 `intake`）被拒绝（§4.3）。

### 3.3 `stock_movements` — 账本（迁移 `0011`）

仅追加的有类型事务日志。**`exact` 批次的当前数量 = 其各移动的 `SUM(quantity_delta)`。**

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `id` | Integer PK | 否 | |
| `instance_id` | FK → `stock_instances.id` | 否 | 受影响批次；**`ondelete="CASCADE"`**（批次的账本随批次消亡 — M2 无软删除）。 |
| `type` | String(20) | 否 | `intake` / `consume` / `move` / `adjust` / `discard` / `correction`；应用层对照 `MOVEMENT_TYPES` 校验。 |
| `quantity_delta` | Numeric(18,6) | 否 | **带符号**：`+` 入库、`−` 消耗/丢弃、`±` 调整/冲正、move 为 **`0`**。 |
| `from_location_id` | FK → `locations.id` | 是 | `ondelete="SET NULL"`；`move` 时设置。 |
| `to_location_id` | FK → `locations.id` | 是 | `ondelete="SET NULL"`；`move` 时设置（`intake` 时记录以备溯源）。 |
| `occurred_at` | DateTime(tz) | 否 | `server_default=func.now()`；实物发生时间（可回填）。 |
| `note` | String(1000) | 是 | 自由文本；富附件/标签是 M5。 |
| `reverses_movement_id` | FK → `stock_movements.id`（自引用） | 是 | `ondelete="SET NULL"`；本条目所冲正的原移动。**部分唯一** `WHERE reverses_movement_id IS NOT NULL`（一笔移动至多被冲正一次）。 |
| `user_id` | FK → `users.id` | 是 | `ondelete="SET NULL"`；操作用户（M6 审计脊梁）。 |
| `created_at` | DateTime(tz) | 否 | 行创建。 |

索引：`ix_stock_movements_instance_id`；`(instance_id, occurred_at)` 上的 `ix_stock_movements_instance_occurred`（用于历史排序）；`reverses_movement_id` 上的部分唯一索引。

> **数据回填（在 `0012`，待表于 `0011` 存在后）。** 每个既有 `stock_instance`（默认全为 `exact`）获得**一笔 `intake` 移动**，`quantity_delta = 其当前数量`、`occurred_at = received_at = created_at`、`user_id = NULL`（系统）。这兑现 M1 §12 的承诺 — 数量变为账本派生而不改变任何显示数字 — 是 `0012` 的 `upgrade()` 的一部分（downgrade 在还原列改动前删除这些移动）。

### 3.4 三种跟踪模式（语义）

| 模式 | `quantity` | `stock_level` | 账本？ | 允许的移动 | 低库存规则 |
|---|---|---|---|---|---|
| **`exact`**（默认） | Decimal，账本派生 | `NULL` | **有** | intake / consume / move / adjust / discard / correction（+ reverse） | 设置了 `min_stock` 时 `SUM(qty) < min_stock` |
| **`level`** | `NULL` | `high`/`medium`/`low`（手动） | 无 | 无（移动端点拒绝） | **任一**批次处于 `low` |
| **`none`** | `NULL` | `NULL` | 无 | 无 | 永不 |

跨字段校验（服务层，§4 + §5）：批次的字段必须与其定义的模式匹配 — `exact` ⇒ 数值数量、无 `stock_level`；`level` ⇒ 设置 `stock_level`、无数量；`none` ⇒ 两者皆无。对非 `exact` 定义的移动被拒绝。**已有批次时切换定义模式**受约束（§4.1、§12）。

### 3.5 迁移清单

| 修订 | 步骤 | 做什么 |
|---|---|---|
| `0010` | 1 | `item_definitions`：新增 `stock_tracking_mode`（默认 `exact`）+ `min_stock`。 |
| `0011` | 2 | 创建 `stock_movements`（账本）。 |
| `0012` | 3 | `stock_instances`：新增 `stock_level` + `received_at`；**batch-alter** `quantity` → 可空并改写 serial CHECK；**回填**每个既有批次一笔 `intake` + 置 `received_at = created_at`。 |

均可逆（downgrade 反向撤销；`0012` 的 downgrade 删除回填的移动、batch 还原 CHECK + `quantity NOT NULL`、删除两个新增列）。

---

## 4. 后端设计

### 4.1 仓储 + 服务分层（扩展 M1）

- **仓储**（`app/repositories/`）：新增 `StockMovementRepository`（`append`、`get`、`list_for_instance`、`sum_delta_for_instance`、`find_reversal_of`、`delete_for_instance`）。扩展 `StockInstanceRepository`：`sum_quantity_for_definition`、`list_active_lots_for_definition`（quantity > 0，按 `received_at, id` 排序）。`ItemDefinitionRepository` 仅按低库存扫描所需扩展。**纯数据访问；无业务规则；路由中无裸查询。**
- **服务**（`app/services/`）：
  - `StockInstanceService`（扩展）：模式感知的创建/更新；`recompute_quantity(instance)` 辅助（`= sum_delta_for_instance`）；`exact` 模式创建时记录**初始 `intake`**；跨字段/模式校验；重算后再校验 serial⇒qty=1。
  - **`StockMovementService`**（新）：各操作 — `intake`、`discard`、`adjust`、`move`、`consume_fifo`、`reverse`。每个追加移动、在**同一事务内**重算受影响批次，并强制模式/serial/非负护栏。这里是**易错逻辑**的归宿（§5）。
  - **`LowStockService`**（新，小）：计算式低库存扫描（§4.5）。
- **已填充定义的模式变更：** 当定义已有批次、其字段在新模式下会变得非法时（例如 `exact`→`none` 而批次带有账本历史），服务**拒绝**变更 `stock_tracking_mode` → `item_definition.tracking_mode_change_conflict`（409）。干净的重建模采推迟（§12）。

### 4.2 数量派生（缓存 + 重算）

唯一规则，在每处写入移动的地方应用：

```
在一个 DB 事务内（每请求）:
    repo.append(movement)                    # 账本是唯一真相源
    inst.quantity = repo.sum_delta_for_instance(inst.id)   # 重算缓存
    # 绝不: inst.quantity = inst.quantity + delta   (不盲目覆盖 — roadmap §2.3)
    assert_serial_qty_1(inst.serial, inst.quantity)        # 带序列号批次保持为 1
```

`sum_delta_for_instance` 是 `SELECT COALESCE(SUM(quantity_delta), 0)` — 全程 `Decimal`（绝不用 float，roadmap §2.9）。不变式 `quantity == SUM(deltas)` 在每个操作序列后由测试断言（§5）。

### 4.3 移动操作与 FIFO

- **`intake(instance, quantity, occurred_at?, note?)`** — 追加 `intake`（`+quantity`），重算。对带序列号批次，会超过 `1` 的入库被拒绝。创建实例（`POST /instances`，`exact`）记录**初始**入库。
- **`discard(instance, quantity, ...)`** — 追加 `discard`（`−quantity`）；会变负则拒绝。
- **`adjust(instance, counted_quantity, ...)`** — 盘点到**绝对**值：`delta = counted_quantity − current`；追加 `adjust`（带符号）；`counted_quantity < 0` 则拒绝。
- **`move(instance, to_location_id, ...)`** — 追加 `move`（`delta = 0`、`from = inst.location_id`、`to = to_location_id`）；置 `inst.location_id = to_location_id`。仅整批（§2）。`to_location_id` 必须存在。
- **`consume_fifo(definition, quantity, ...)`** — 重头戏：
  ```
  断言 definition.mode == 'exact'
  lots = repo.list_active_lots_for_definition(def.id)   # quantity > 0, ORDER BY received_at, id
  if sum(lot.quantity for lot in lots) < quantity: raise stock.insufficient (422)
  remaining = quantity
  for lot in lots:
      take = min(lot.quantity, remaining)
      movement(lot, type=consume, delta = -take, occurred_at)
      recompute(lot)
      remaining -= take
      if remaining == 0: break
  ```
  对每个被触及批次产生一笔 `consume` 移动；从最旧开始跨多个批次；部分批次消耗为精确 `Decimal`。
- 所有操作拒绝非 `exact` 模式的定义/实例（`stock.movement_not_applicable`，409）。

### 4.4 冲正 / 撤销

```
reverse(movement_id, note?):
    m = get(movement_id) or 404
    if m.reverses_movement_id is not None: raise stock.cannot_reverse_reversal (409)
    if repo.find_reversal_of(m.id) is not None: raise stock.movement_already_reversed (409)
    inst = m.instance
    # 计算预期新数量；会变负则拒绝
    if inst.quantity - m.quantity_delta < 0: raise stock.reverse_would_go_negative (409)
    r = movement(inst, type=correction, delta = -m.quantity_delta,
                 reverses_movement_id = m.id, occurred_at = now, note)
    if m.type == 'move':                       # 同时还原库位
        r.from_location_id, r.to_location_id = m.to_location_id, m.from_location_id
        inst.location_id = m.from_location_id
    recompute(inst)
```

🟢「撤销恢复」路径 = 冲正那笔 `consume`。非负护栏意味着：在库存被消耗后再撤销一笔旧 `intake` 会被拒绝（保持账本一致）；撤销**最近一次**操作总会成功。`reverses_movement_id` 上的部分唯一索引是「只一次」规则的 DB 兜底。

### 4.5 低库存（计算式）

`GET /low-stock` → 一份低库存定义清单，带原因与 UI 所需数字：

```
for def in 所有定义:
    if def.mode == 'exact' and def.min_stock is not None:
        total = repo.sum_quantity_for_definition(def.id)
        if total < def.min_stock:
            yield { definition_id, name, mode:'exact', reason:'below_min_stock',
                    current: total, threshold: def.min_stock }
    elif def.mode == 'level':
        if 任一 lot.stock_level == 'low':
            yield { definition_id, name, mode:'level', reason:'level_low',
                    current: null, threshold: null }
    # none → 跳过
```

纯读取；无持久化。M4 将*消费*该信号做主动提醒。

### 4.6 API 面（均在 `settings.api_prefix` 下，默认 `/api`；均经 `get_authenticated_context` 鉴权）

| 方法 + 路径 | 用途 |
|---|---|
| `POST /definitions` · `PATCH /definitions/{id}` | 现也接受 `stock_tracking_mode` + `min_stock`（模式变更有护栏） |
| `POST /instances` | `exact`：记录初始 `intake`；`level`：要求 `stock_level`；`none`：仅在册 |
| `PATCH /instances/{id}` | **无 `quantity`** 字段；`stock_level` 可设（`level` 模式）；序列号/库位/耐用品字段同 M1 |
| `GET /instances/{id}/movements` | 该批次的账本历史（最新在前），含冲正链接 |
| `POST /instances/{id}/intake` | `{ quantity, occurred_at?, note? }` → `+qty` |
| `POST /instances/{id}/discard` | `{ quantity, occurred_at?, note? }` → `−qty` |
| `POST /instances/{id}/adjust` | `{ quantity（绝对盘点值）, occurred_at?, note? }` |
| `POST /instances/{id}/move` | `{ to_location_id, occurred_at?, note? }`（整批） |
| `POST /definitions/{id}/consume` | `{ quantity, occurred_at?, note? }` → 跨该定义批次 FIFO |
| `POST /movements/{id}/reverse` | `{ note? }` → 追加冲正 |
| `GET /low-stock` | 计算式低库存清单（原因 + 当前/阈值） |

错误契约（经 M1.5 信封，§4.7）：`404` 缺失 id；`409` 模式/冲正冲突；`422` 库存不足、非法数量、校验、模式/字段不匹配；`401` 无会话。

### 4.7 错误码（加入 M1.5 的 `ErrorCode` 注册表，`app/core/errors.py`）

| 码 | 状态 | 何时抛出 | `params` |
|---|---|---|---|
| `stock.insufficient` | 422 | `consume` 超过可用总量 | `{requested, available}` |
| `stock.negative_quantity` | 422 | 入库/丢弃/调整会把批次驱为 `< 0`，或输入为负 | `{id}` |
| `stock.movement_not_applicable` | 409 | 对 `level`/`none` 定义做移动 | `{id, mode}` |
| `stock.movement_not_found` | 404 | `reverse` 一笔缺失的移动 | `{id}` |
| `stock.movement_already_reversed` | 409 | 目标已被冲正 | `{id}` |
| `stock.cannot_reverse_reversal` | 409 | 目标本身是一笔冲正 | `{id}` |
| `stock.reverse_would_go_negative` | 409 | 冲正会把批次驱为 `< 0` | `{id}` |
| `instance.field_mode_mismatch` | 422 | 数量/`stock_level` 与定义模式不匹配 | `{mode, field}` |
| `validation.unsupported_tracking_mode` | 422 | 非法 `stock_tracking_mode` | `{value, supported}` |
| `validation.unsupported_stock_level` | 422 | 非法 `stock_level` | `{value, supported}` |
| `item_definition.tracking_mode_change_conflict` | 409 | 对已填充定义变更模式 | `{id, from, to}` |

`message` 仍为面向开发者的英文；**前端**经 `errors` 命名空间本地化每个码（wire/display 分离，M1.5）。

### 4.8 Pydantic schema（`app/schemas/`）

- `DefinitionCreate/Update/Response` 新增 `stock_tracking_mode` + `min_stock`（`Decimal | None`）。
- `InstanceCreate` 新增 `stock_level`；保留 `quantity`（初始入库，`exact`）。`InstanceUpdate` **移除 `quantity`**，新增 `stock_level`。`InstanceResponse` 新增 `stock_level` + `received_at`，`quantity` 变为 `Decimal | None`。
- 新增：`MovementResponse`（完整账本行，含 `type`、`quantity_delta`、库位、`occurred_at`、`reverses_movement_id`、`user_id`）；操作体 `IntakeRequest` / `DiscardRequest` / `AdjustRequest` / `MoveRequest` / `ConsumeRequest` / `ReverseRequest`；`LowStockItem`（§4.5 的形状）。所有数量/金额在线上为 `Decimal`（roadmap §2.9）。

---

## 5. 质量门禁与易错逻辑（完成定义）

继承 M0/M1/M1.5 §5（`make check` = ruff + mypy + pytest + eslint + tsc + vitest 全绿；构建通过；`make codegen` 无漂移）。M2 中**必须有单元测试的逻辑**（roadmap DoD — 数量计算、库存进出、消耗顺序、阈值触发）：

**后端**
- **账本不变式：** 任意操作序列后 `instance.quantity == SUM(quantity_delta)`；缓存**绝不**被盲目赋值。
- **入库 / 丢弃 / 调整：** 入库增加；丢弃减少；丢弃/调整降至 `0` 以下被拒（`stock.negative_quantity`）；调整计算到绝对盘点值的正确带符号 delta。
- **FIFO 消耗：** 单批次；**跨多批次从最旧开始**按 `(received_at, id)`；部分批次 `Decimal` 精度；**总量不足被拒**（`stock.insufficient`）且不写入任何数据；`level`/`none` 定义被拒。
- **移动：** 整批库位变更，记录 `from`/`to`、`delta = 0`；不存在的 `to_location_id` → 404；数量不变。
- **冲正 / 撤销：** 冲正一笔 `consume` 恢复数量（🟢 路径）；一笔移动被冲正两次 → `stock.movement_already_reversed`；冲正一笔冲正 → `stock.cannot_reverse_reversal`；会变负的冲正 → 拒绝；冲正一笔 `move` 还原 `location_id`。
- **账本下的 serial ⇒ qty=1：** 会把带序列号批次推过 `1` 的 `intake` 被拒；改写后的 DB CHECK 仍阻止直接坏写（`serial IS NULL OR quantity IS NULL OR quantity = 1`）。
- **模式：** `exact` 创建记录初始入库；`level` 要求 `stock_level` 且禁止数量/移动；`none` 禁止数量与 `stock_level` 及一切移动；字段/模式不匹配 → `instance.field_mode_mismatch`；对已填充定义变更模式 → 409。
- **低库存：** `exact` 低于/等于/高于 `min_stock`（严格小于边界）；`exact` 未设 `min_stock` → 永不标记；`level` 低 vs 不低；`none` 永不；混合集合返回正确原因 + 数字。
- **迁移往返：** `0010`–`0012` 在 `0009` 的 DB 上干净升级、干净降级，**包括 `0012` 的 batch-alter（CHECK 改写 + 可空 quantity）与入库回填**（及降级时删除）。

**前端**（vitest + Testing Library，mock 有类型客户端 — M0 风格）：定义表单模式切换显隐 `min_stock`；实例表单按模式分支（数量 vs `stock_level` vs 皆无）并保留客户端 serial⇒qty=1 规则；consume/intake/adjust/move/discard 调用正确端点并经 `mapApiError` 浮现服务端错误；移动历史渲染；冲正动作可用；低库存仪表盘磁贴渲染计数 + 清单。

---

## 6. 契约优先代码生成与无漂移门禁

机制不变（M0 §6 / M1 §6）：每个触及 API 的步骤**重跑 `make codegen`**（重生成仓库根 `openapi.json` + `frontend/src/api/schema.d.ts`）并提交两者；CI **contract** 作业（`make codegen` + `git diff --exit-code`）在漂移时失败。M2 以操作端点、`MovementResponse`/`LowStockItem`、新的定义/实例字段与新错误码扩展 schema；前端经既有 `openapi-fetch` 客户端消费新的有类型路径。

---

## 7. 前端设计

### 7.1 定义表单与详情（模式 + `min_stock`）

- `DefinitionFormModal`（在 `pages/Items.tsx`）新增一个 **`stock_tracking_mode`** `Select`（exact / level / none，本地化）与一个**仅当模式 = exact 时显示**的 **`min_stock`** `NumberInput`。定义详情卡显示模式（一枚徽标）与 `min_stock`。
- 编辑一个已有批次的定义的模式会经 `mapApiError` 浮现 409（`tracking_mode_change_conflict`）。

### 7.2 实例表单（模式感知）— `components/InstanceFormModal.tsx`

该模态已拥有客户端 serial⇒qty=1 规则（M1 §7.3）。M2 让它**基于父定义的模式分支**（与既有 `definitions`/`locations` props 一并传入）：
- **exact：** `quantity` 字段（创建时为初始入库；**编辑时隐藏/锁定** — 数量变化走账本动作）+ serial⇒qty=1。
- **level：** 一个 `stock_level` `Select`（high/medium/low）；无数量。
- **none：** 皆无；仅身份/库位/耐用品字段。

### 7.3 账本动作 + 移动历史（`ItemDetail` + `InstanceDetail`）

- **`ItemDetail`**（exact 定义）：一个 **Consume** 按钮（FIFO，`POST /definitions/{id}/consume`）带数量输入；实例表格显示每个批次的当前 `quantity`（或按模式显示 `level` 徽标 / 「—」）与每批次动作菜单（**Intake / Move / Adjust / Discard**）。定义低于 `min_stock` 时显示**低库存徽标**。
- **`InstanceDetail`**：一张**移动历史**表（类型、带符号 delta、from→to、occurred_at、操作者、「冲正 #N」链接），每个可冲正行带 **Reverse（撤销）** 动作，外加相同的每批次动作按钮。数量/等级按模式渲染。
- 所有数量在线上以**字符串**（Decimal）传输；经 `formatQuantity` / `formatDate`（M1.5 区域感知）渲染。

### 7.4 低库存仪表盘磁贴 + 视图

- `Dashboard.tsx`：消耗品卡变为一个**实时低库存磁贴** — 来自 `GET /low-stock` 的计数与一份短清单（定义名 + 当前/阈值或「低」），链接到一个低库存视图（筛选区段/页面）。无低库存时显示空态。

### 7.5 i18n

在 `en` 与 `zh` 两套目录都加新串（M1.5 规则）：一个新的 **`stock`** 命名空间（移动类型、操作标签、FIFO/消耗文案、冲正、低库存）在 `src/i18n/index.ts` 注册；对 `items`（模式/min_stock/等级）、`instances`（历史、动作）、`dashboard`（低库存磁贴）的增补，以及 `errors` 下的新**错误码**。测试仍钉在 `en`（M1.5）。

### 7.6 测试（vitest + Testing Library）

按 §5「前端」：模式切换字段显隐；实例表单模式分支 + serial 规则；每个账本动作命中正确端点并浮现错误；移动历史 + 冲正渲染；低库存磁贴。保持 M0「mock 有类型客户端」风格。

---

## 8. CI 与 Docker（相对 M1 的增量）

无结构性变化。**docker** 冒烟测试的迁移步骤现对全新绑定挂载 DB 应用 `0001`–`0012`（原为 `0001`–`0009`）。其余（缓存、契约门禁、单镜像、fail-closed `migrate` 服务）与 M0 §8 完全一致。

---

## 9. 步骤拆解（原子、有序）

每步独立可测、有测试支撑、恰落一个提交（编排模式下每步一次 autosquash）、若触及 API 则重跑 `make codegen`，并继承全局 DoD（§5）+ **反自由发挥规则**：只实现*本*步 — 不做其他步、不夹带重构、不碰宿主真实环境。

> **排序理据：** schema 优先，按 FK/依赖顺序 — 定义列（`0010`）与账本表（`0011`）必须先于写入账本的实例改动 + 回填（`0012`）存在。后端操作（4）与低库存读取（5）坐落于该 schema 之上。前端（6–8）消费后端落定的有类型契约。

### 步骤 1 — 定义：跟踪模式 + min_stock（后端）
- **目标：** 每定义的 `stock_tracking_mode`（默认 `exact`）+ `min_stock`，已校验并上契约。
- **构建：** 迁移 `0010`；`STOCK_TRACKING_MODES` + `STOCK_LEVELS` 常量（一个小 `app/core/stock.py` 或扩展既有 core 模块）；扩展 `ItemDefinition` 模型 + `DefinitionCreate/Update/Response`；`ItemDefinitionService` 校验模式并存储 `min_stock`；新错误码 `validation.unsupported_tracking_mode`；`make codegen`。
- **测试：** 默认模式 `exact`；非法模式被拒（422）；`min_stock` 存储/回显；迁移 `0010` 升/降。
- **范围外：** 账本、实例改动、已填充定义的模式变更护栏（此时批次尚无移动 — 护栏随服务在步骤 4 落地 / 在批次获得账本处细化）。
- **提交：** `feat(backend): per-definition stock tracking mode and min_stock`

### 步骤 2 — 库存移动账本表 + 仓储（后端）
- **目标：** 仅追加的账本表及其纯数据访问层。
- **构建：** `models/stock_movement.py`（§3.3 全部列、自引用 FK、`reverses_movement_id` 上的部分唯一、`instance_id` 上 CASCADE）；迁移 `0011`；`StockMovementRepository`（`append`、`get`、`list_for_instance`、`sum_delta_for_instance`、`find_reversal_of`、`delete_for_instance`）；`MovementResponse` schema；`MOVEMENT_TYPES` 常量；`make codegen`（仅 schema — 尚无端点）。
- **测试：** 迁移 `0011` 升/降；仓储 append/list/sum/`find_reversal_of`；部分唯一在 DB 层阻止第二条冲正行。
- **范围外：** 任何服务操作或端点（步骤 4）。
- **提交：** `feat(backend): stock movement ledger table and repository`

### 步骤 3 — 实例改动 + 账本接线 + 回填（后端）
- **目标：** 让 `exact` 数量由账本派生、加 `stock_level`/`received_at`、按模式分支实例 CRUD、并回填既有行。
- **构建：** 迁移 `0012`（加 `stock_level` + `received_at`；batch-alter `quantity`→可空 + 改写 serial CHECK；回填每个既有批次一笔 `intake` + `received_at = created_at`）；更新 `StockInstance` 模型；更新 `Instance{Create,Update,Response}`（Update 去掉 `quantity`；加 `stock_level`；Response 中 quantity 可空）；`StockInstanceService` — 模式感知创建（`exact` 记录初始 `intake`；`level` 要求 `stock_level`；`none` 无）、`recompute_quantity` 辅助、serial⇒qty=1 复检、字段/模式校验；错误码 `instance.field_mode_mismatch`、`validation.unsupported_stock_level`；`make codegen`。
- **测试：** 按模式创建；回填正确性（数量不变、各一笔入库）；不变式 `quantity == SUM(deltas)`；可空数量下 serial⇒qty=1 成立（服务 422 + DB CHECK）；字段/模式不匹配被拒；迁移 `0012` 升/降含 batch-alter + 回填（且降级删除回填的移动）。
- **范围外：** 操作端点 + FIFO/冲正（步骤 4）；低库存（步骤 5）。
- **提交：** `feat(backend): ledger-derived quantity, stock levels, and intake backfill`

### 步骤 4 — 移动操作服务 + 端点（后端）
- **目标：** 在账本之上做 intake / discard / adjust / move / FIFO-consume / reverse。
- **构建：** `StockMovementService`（§4.3–4.4：每个操作在同一事务内追加 + 重算；FIFO；冲正规则；非负 + 模式 + serial 护栏；已填充定义的模式变更护栏此时有意义 → `tracking_mode_change_conflict` 接入 `ItemDefinitionService`）；请求 schema（`IntakeRequest`/`DiscardRequest`/`AdjustRequest`/`MoveRequest`/`ConsumeRequest`/`ReverseRequest`）；路由（`/instances/{id}/{intake,discard,adjust,move}`、`/definitions/{id}/consume`、`/movements/{id}/reverse`、`GET /instances/{id}/movements`）；§4.7 的 stock 错误码；`make codegen`。
- **测试（易错逻辑 — 按 §5 必需）：** FIFO（单/多批/部分/不足/精度）；intake/discard/adjust（含负数拒绝）；move（库位 + delta 0 + 历史）；reverse（恢复、双重冲正、冲正之冲正、会变负、move 还原）；经 intake 的 serial⇒qty=1；对 `level`/`none` 拒绝移动。
- **范围外：** 低库存（步骤 5）；前端。
- **提交：** `feat(backend): stock movement operations, FIFO consume, and reversal`

### 步骤 5 — 低库存计算端点（后端）
- **目标：** 跨模式的 `GET /low-stock` 信号。
- **构建：** `LowStockService`（§4.5）+ 仓储辅助（`sum_quantity_for_definition`、任一批次低）；`LowStockItem` schema；`GET /low-stock` 路由；`make codegen`。
- **测试：** `exact` 低于/等于/高于 `min_stock`；无 `min_stock`；`level` 低/不低；`none` 永不；混合。
- **范围外：** 前端磁贴（步骤 8）。
- **提交：** `feat(backend): computed low-stock endpoint`

### 步骤 6 — 前端：定义模式/min_stock + 模式感知实例表单
- **目标：** 在 UI 中编写两个契约字段并按模式分支实例表单。
- **构建：** `DefinitionFormModal` 模式 `Select` + 条件 `min_stock`；定义详情显示模式/min_stock；`InstanceFormModal` 按父定义模式分支（数量 vs `stock_level` vs 皆无；编辑时锁定数量）；新 `stock` 命名空间 + `items`/`instances` 串（en+zh）在 `src/i18n/index.ts` 注册；在 `errors` 中映射新错误码（en+zh）。
- **测试：** 模式切换字段显隐；实例表单分支 + serial 规则；模式变更 409 浮现。
- **范围外：** 账本动作（步骤 7）；低库存磁贴（步骤 8）。
- **提交：** `feat(frontend): definition tracking mode, min_stock, and mode-aware instance form`

### 步骤 7 — 前端：账本动作 + 移动历史
- **目标：** 在 UI 中实现 intake/consume/move/adjust/discard 流程 + 历史 + 撤销。
- **构建：** `ItemDetail` Consume（FIFO）+ 每批次动作菜单 + 低库存徽标 + 每批次数量/等级渲染；`InstanceDetail` 移动历史表 + 每批次动作 + Reverse（撤销）；`stock`/`instances` 串（en+zh）。
- **测试：** 每个动作命中正确端点 + 经 `mapApiError` 浮现错误；历史渲染；冲正可用。
- **范围外：** 仪表盘磁贴（步骤 8）。
- **提交：** `feat(frontend): stock ledger actions, FIFO consume, and movement history`

### 步骤 8 — 前端：低库存仪表盘磁贴 + 视图
- **目标：** 在仪表盘上浮现低库存信号。
- **构建：** `Dashboard.tsx` 实时低库存磁贴（来自 `GET /low-stock` 的计数 + 短清单，空态）链接到低库存视图/区段；`dashboard` 串（en+zh）。
- **测试：** 磁贴渲染计数 + 清单；空态；链接跳转。
- **范围外：** 消费该信号的 M4 提醒引擎。
- **提交：** `feat(frontend): low-stock dashboard tile`

> 步骤 6/7 可拆（定义 vs 实例），步骤 3 可拆（迁移 vs 服务接线），若过大；保持各自独立绿色。

---

## 10. 盲审检查点（逐步）

审查者**仅**获得：本文档 + roadmap、该步实现简报、该步 diff。检查：

- **步骤 1：** 模式在**应用层**对照 `STOCK_TRACKING_MODES` 校验（无 DB CHECK / 列类型中无内嵌 string-enum）；默认 `exact`；`default_best_before_days` **未**提前夹带；codegen 已提交。
- **步骤 2：** 账本为**仅追加**（仓储无 update 方法）；`instance_id` 上 CASCADE；`reverses_movement_id` 上部分唯一在场；`quantity_delta` 为 `Numeric`/`Decimal`（无 float）；迁移可逆；尚无服务/端点。
- **步骤 3：** 数量重算为 `SUM(deltas)`、**绝不** `+= delta`；serial CHECK 改写正确且服务仍强制 serial⇒qty=1；回填对每个批次恰建一笔 `intake`、delta 正确且不改变显示数量；可空数量的 batch-alter 正确；按模式创建正确；downgrade 删除回填的移动。
- **步骤 4：** FIFO 按 `(received_at, id)` 排序，且不足时**不写入任何数据**而拒绝（事务完整性）；冲正只一次 + 不可冲正冲正 + 非负护栏 + 还原 `move` 库位；所有操作拒绝非 `exact` 定义；无业务逻辑泄漏进路由/仓储；codegen 已提交。
- **步骤 5：** 低库存对 `exact` 用 **`< min_stock`**、对 `level` 用**任一批次低**，`none` 跳过；纯读取（无写入）；codegen 已提交。
- **步骤 6：** `min_stock` 仅对 `exact` 显示；实例表单正确分支并保留客户端 serial⇒qty=1 规则；使用有类型客户端（无手写 fetch）；`en`+`zh` 两套目录都更新（无缺键）。
- **步骤 7：** 数量以**字符串**（Decimal）发送；每个动作调用文档化端点；撤销浮现正确结果；仅 cookie 鉴权不变。
- **步骤 8：** 磁贴读取 `GET /low-stock`（不在客户端重新推导规则）；处理空态。
- **横切：** 符合 roadmap §2（尤其 **§2.3 账本派生/绝不覆盖**、§2.9 Decimal、§2.11 逻辑在应用层而非 SQL）、M1.5 统一错误信封（新码已注册、无裸 `detail`），以及本文档变更时的**双语文档规则**。

---

## 11. 🟢 部署自测点（拼入 M2 里程碑走查）

作者在里程碑末运行的手动走查（展开 roadmap M2 🟢 条目）。假定 M1 的运行流程（compose up；以管理员登录；已存在一棵库位树 + 一个分类）：

1. **消耗品定义（exact）：** 创建定义 `AA 电池`（模式 = exact，单位 = `pcs`，`min_stock = 4`）。
2. **入库 10 → FIFO 消耗 3 → 派生 7：** 登记一个初始数量 **10** 的 `AA 电池` 批次（其历史中出现一笔 +10 的 `intake`）。对该定义 **消耗 3**（FIFO）→ 批次派生数量为 **7**；账本中可见两行 `consume`/`intake`。
3. **撤销恢复：** **冲正**该消耗 → 数量回到 **10**；历史显示一笔链接到该消耗的冲正。
4. **低库存浮现：** 消耗到 **3**（低于 `min_stock = 4`）→ **仪表盘低库存磁贴**列出 `AA 电池`，当前 3 / 阈值 4。再用一次 **入库**补满 → 它离开磁贴。
5. **移动（整批）：** 把批次 **移动**到另一库位 → 其 `location_id` 改变并出现一行 `move`（from→to，delta 0）；冲正该移动还原原库位。
6. **调整（盘点）：** 把批次 **调整**到一个绝对盘点值（例如 5）→ 一行带正确带符号 delta 的 `adjust`；数量变为 5。
7. **level 模式：** 创建定义 `各类螺丝`（模式 = level）；登记一个 `stock_level = low` 的批次 → 它出现在低库存磁贴（原因：低）；不提供数量/账本动作。
8. **none 模式：** 创建定义 `墙面装饰画`（模式 = none）；登记一个实例 → 无数量、无等级、无账本；永不低库存。
9. **账本下的 serial ⇒ qty=1：** 对一个带序列号的 exact 批次（qty 1），会把它推过 1 的 **入库**被**拒绝**（UI 中 422）；DB CHECK 独立阻止直接坏写。
10. **CI 绿：** 同样的门禁在 GitHub Actions 通过，包括无漂移契约门禁；迁移 `0001`–`0012` 在 docker 冒烟测试的全新 DB 上干净应用。

---

## 12. 开放问题 / 推迟

- **低库存边界 `<` vs `≤`：** M2 在 **`total < min_stock`** 标记 `exact` 低库存（对应 roadmap 的「降到其下」）。若作者偏好再订货点语义（`≤`，即*处于*最小值即触发再订货），是一行的改动 — 在手动走查时确认。
- **部分拆分移动：** M2 仅整批移动。把批次的*部分*数量移入目标库位的新批次（源处消耗 + 目标处入库，关联）推迟 — 待消耗品 UX 需要时重审（可能配合 M7 购物清单流程）。
- **已填充定义的模式变更：** M2 在存在不兼容批次时**阻止**变更 `stock_tracking_mode`（409）。一个引导式重建模流程（例如 exact→level 经确认丢弃账本）推迟 — 与 M5/M6 的软删除/归档方向自然成对（M1 §12）。
- **批次与移动的软删除/归档：** M2 硬删除（删批次级联其账本）。保留审计的方向（软删除，沿用 M1 §12）随 M5/M6 重审。
- **`level` 粒度：** M2 用固定的 `high`/`medium`/`low`。该集合是否应可由用户配置，以及 `level` 是按批次（如现状）还是应聚合到定义，留待开放 — 若实际使用出现摩擦再审。
- **M3 中的 FIFO 键：** M2 按 `(received_at, id)` 排序；M3 **前置 `best_before`** 为首要键，使易腐品近效期先消耗。M2 的 `consume_fifo` 写法使 M3 只需改 `ORDER BY`。
- **从特定批次消耗：** M2 仅暴露定义级 FIFO 消耗（+ 每批次 `discard`/`adjust`）。如需可后续加一个批次级消耗。
- **`received_at` 粒度：** 以 `DateTime(tz)` 存储；UI 可能只暴露日期。若日内精确排序某天变得重要再审（今日由 id 破平）。
