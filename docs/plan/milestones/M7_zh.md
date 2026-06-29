# M7 — 购物清单与养护计划 (③ / ②)

> 🌐 **语言:** [English](./M7.md) · 中文(当前)

> **里程碑设计文档 — 自包含。** 请与 `docs/plan/roadmap.md`(总图,尤其 §5 M7 + 红线 **§2.3 数量由账本派生 / 绝不盲目覆盖**、**§2.6 一个统一提醒引擎、多来源** —— 养护到期正是引擎*预留*要接入的来源、**§1.2 单租户 / 全体用户共享数据**、**§2.10 单一上下文 / 仓储层**、**§2.11 逻辑在应用层而非数据库**)对照阅读;关于"为何存在",见 `docs/inspiration/investigation.md`。本文是 *M7 构建什么、如何验收* 的唯一真相源;不要再从 roadmap 反推范围。进度**仅**记录在 roadmap §4 表。
>
> 已内建的约定:原子步骤(§9)、盲评检查点(§10)、🟢 部署自测点(§11),使手动与编排两种执行都可挂靠本文。
>
> **范围提示(先读)。** M7 闭合核心模型留下的两个回路:**消耗品回路**(低库存 → 一份真实持久化、可勾选的**购物清单**,勾选可经 M2 入库账本把库存补回)与**耐用品养护回路**(耐用品上的循环**养护计划**,经 M4 提醒引擎作为其长期预留的**养护到期来源**触发)。分**三阶段**(A 购物清单、B 养护 + 提醒来源、C 前端)共 **8 个原子步骤**:一张由实时低库存信号自动对账 + 自由文本手动条目构成的 `shopping_list_items` 表;勾选可经**现有**账本路径把已购库存入库;一张带日历正确循环、并有"标记完成 → 顺延"流程的 `maintenance_schedules` 表;`ReminderEngine` 中**纯加法**的**养护来源**(按耐用品的负责人路由,M6);以及购物清单 / 养护 / 仪表盘 UI。M7 还**预留 —— 但不实现 —— 外部待办同步接缝(TickTick)**:见 **§12**。

---

## 1. 目标与非目标

**目标(roadmap 的 M7 承诺 —— 闭合消耗品回路与耐用品养护回路):**

- **购物清单** —— 一份持久化、**家庭共享**的清单(单租户,全体用户共享数据 —— roadmap §1.2)。它**由实时低库存信号自动填充**(`LowStockService`,M2),也接受**自由文本手动条目**。条目可**勾选**;勾选一条绑定定义的条目可**经现有 M2 账本把已购数量入库**(不新增任何库存路径 —— roadmap §2.3)。当定义恢复到阈值之上时,自动行**自我清理**。
- **养护计划** —— 挂在**耐用品库存实例**(具体物理单元:"汽车每 6 个月保养"、"空调滤网每 3 个月更换")上的循环养护任务。每个计划带**日历正确的循环**(每 N 天/周/月/年)、一个 `next_due_date`、一个提前提醒**lead**,以及一个**"标记完成 → 顺延"**动作,记录完成并把 `next_due_date` 向前滚动。
- **养护到期提醒来源** —— M4 预留要接入的来源(M4 §1/§2 "养护到期在 M7 加入;引擎按来源可插拔构建,以便它顺势接入")。它是 `ReminderEngine.run_scan()` 中一个**加法式**评估 pass —— 在 `next_due_date` 前 "N 天"触发,**路由到耐用品的有效负责人**(M6 §4.4),按每次到期去重,并像其它三个来源一样在站内 + 各外部渠道(邮件摘要 / HTTP / MQTT)渲染。**对 best_before / warranty / 低库存无任何改动。**

**完成判定(🟢,§11 展开):** 把某消耗品消耗到 `min_stock` 以下,看它**自动出现在购物清单**;手动加一条自由文本条目;**勾选一条并入库**已购数量,它经真实 `intake` 移动落入库存,(因为该定义不再低于阈值)**从清单上消失**;在某耐用品上创建一个 `next_due_date` 落在 lead 窗口内的养护计划,**运行扫描**,在铃铛/收件箱(及邮件摘要 / MQTT / HTTP)收到一条**养护提醒**,路由到该耐用品的负责人;**标记养护完成**,看 `next_due_date` 按周期向前滚动、提醒清除。CI 保持绿,含无漂移契约门;迁移 `0001`–`0034` 在全新库上干净应用。

**非目标(明确排除在 M7 之外 —— 推迟或后续里程碑):**
- **不做外部待办清单集成(TickTick 等)。** M7 **预留接缝**(§12)但**不发任何 TickTick 专有代码**。真正的 OAuth2 + 双向同步实现归集成里程碑家族(M9 一带)—— 见 §12 与 roadmap §6/§7。
- **不做基于用量/里程表的养护**("每 10 000 公里")。M7 循环仅**基于时间/日历**。用量表(里程、运行小时)是已记的改进项(§13)—— 它需要一个 M7 未建模的读数概念。
- **不做养护完成历史账本。** 计划只存**最近一次**完成(`last_completed_date` + 重算)。完整的逐次完成审计表(类似 M2 库存账本)是已记的推迟项(§13)。
- **不做按定义的养护计划。** 计划挂在**库存实例**(具体耐用品)。定义级"适用于每个单元"的计划推迟(§13);需要时会镜像 M6 负责人"两边都挂"的形态。
- **不新增库存语义。** 勾选入库**委托给现有 M2 `StockMovementService` / 实例创建路径** —— 绝不重写数量计算或账本(roadmap §2.3)。部分拆分移动与按批消耗维持 M2 现状。
- **不做多清单 / 分类 / 货架顺序 / 分派购物。** 一个家庭,一份购物清单(roadmap §1.2)。多个命名清单、按店分组、把购物指派给某人都属停车场(§13)。
- **不做超出 M6 已给的按用户购物清单或按用户养护路由旋钮。** 养护路由逐字复用 M6 负责人链 + 回退全员;不新增路由模型。

---

## 2. 已锁定决策(M7 规划期敲定;依据见 roadmap §2 + M4 §2 + M6 §2)

| 领域 | 决策 |
|---|---|
| **购物清单是*持久化表*,不是计算视图** | 一份计算式"显示哪些低了"的清单无法承载 roadmap M7 要求的**手动条目**与**勾选状态**。所以用一张真实的 `shopping_list_items` 表,同时承载**`manual`**行(用户录入)与**`auto`**行(由低库存物化)。这镜像 Grocy 的真实购物清单表,并让行携带欲购数量、备注、已购状态。 |
| **自动行靠*对账*,而非实时** | `auto` 行由一个**幂等对账 pass**(`ShoppingListService.reconcile_auto_items()`)插入/删除,它读取 `LowStockService.compute()` —— 与 M4 消费的同一信号。它为每个当前低的定义**开**一条开放自动行:若不存在则创建;若存在一条已勾选的自动行(勾选后未补货),则**重开**它(`purchased_at` 清空),使建议重新浮现(§4.3)。**删除**任何**开放未勾选**、其定义已恢复的自动行(已勾选且已恢复的行留在"已购"区,直到*清空已购*)。**部分唯一索引** `(definition_id) WHERE source='auto'` 是"**每定义恰好一条自动行,不论已购状态**"的数据库兜底 —— 做成与状态无关意味着勾选(只盖 `purchased_at`)永远不会产生第二条自动行,故取消勾选时不会冲突。由 `shopping_list.auto_add_low_stock` 设置控制(默认**true**)。 |
| **对账在低库存已触发的同一钩子上运行(双保险)** | `reconcile_auto_items()` 由 (a) **每日扫描** job、(b) 按需 `POST /reminders/run`、(c) `StockMovementService` 中的**低库存事件钩子** —— 在**全部三条**已调用 `evaluate_low_stock` 的移动路径(`consume_fifo` / `discard` / `adjust`)里,**尽力而为 + savepoint 隔离**,使对账失败永不回滚移动 —— 调用,使消耗品一掉低就**立刻**出现在清单上,与提醒引擎的事件 + 定时双保险一致。**`ReminderEngine` 自身与购物清单保持解耦**(调用方各自调用二者;引擎对购物清单零依赖)。 |
| **勾选 → 入库委托给 M2 账本(不新增库存路径)** | 勾选一条绑定定义、带入库参数的条目,**经现有 `StockInstanceService` 创建路径新建一个库存批次**(它记录初始 `intake` 移动,M2 §2)—— 数量保持**由账本派生、绝不盲设**(roadmap §2.3)。不带入库参数时,勾选只盖 `purchased_at`。二者都在**一个事务**内。入库仅对 **`exact`** 定义有意义,故 `check_off` **预检模式**,对 `level`/`none` 定义以现有 `stock.movement_not_applicable` 拒绝入库(一个小的模式守卫 —— 唯一勾选专有的库存逻辑;建批次本身是纯委托)。 |
| **养护计划挂在*库存实例*(耐用品单元)** | `maintenance_schedules.instance_id` → `stock_instances.id`,`ondelete=CASCADE`(计划随耐用品消亡)。具体单元锚点契合"耐用品"(序列化实例),并让提醒复用 M6 的**按批**负责人链(`实例 → 定义 → 回退全员`)。按定义的计划推迟(§13)。 |
| **循环 = 日历正确的区间(单位 + 数量),在应用层计算** | 计划存 `interval_unit ∈ {day, week, month, year}`(String,应用层校验对 `MAINTENANCE_INTERVAL_UNITS`;无 DB CHECK —— §2.11)+ `interval_count ≥ 1`。**下次到期日**由一个小巧、被单测覆盖的 **`add_interval(d, unit, count)`** 助手计算,带**日历正确的月/年运算与月末夹取**(如 1-31 + 1 月 = 2-28/29)—— 纯标准库,**无新依赖**。这正是 DoD 要求加测的"易错日期计算"(§5)。 |
| **完成模型 = `last_completed_date` + 顺延(不加表)** | "标记完成"置 `last_completed_date = completed_on`(默认今天,可回填),并**顺延 `next_due_date = add_interval(completed_on, unit, count)`**。计划只保留**最近一次**完成;完整完成历史账本推迟(§13),与 M4 "opener 行即 episode、不加表"的极简一致。计划可**暂停**(`is_active=false`)而非删除;暂停的计划被引擎跳过。 |
| **养护是*加法式*提醒来源(M4 接缝)** | `ReminderEngine` 新增一个专门的 `_evaluate_maintenance` pass(与两个 `_DateSource` 评估器及低库存 pass 并列)—— **现有来源原封不动**。它在 `today_local ≥ next_due_date − lead` 时触发,按每次到期去重(`maintenance:u{uid}:s{sid}:{next_due_date}`),经**实例的**有效负责人路由(`_effective_responsible_for_lot(schedule.instance)`,M6),写入 `source="maintenance"`、`subject_type="maintenance_schedule"`、`subject_id=schedule.id`、`message_code="reminder.maintenance"` 的通知。`ScanSummary` / `ReminderRunSummary` 新增 `maintenance` 计数。 |
| **养护 lead 解析:按计划 → 全局(无按用户)** | `resolve_maintenance_lead = schedule.lead_days if not None else settings.maintenance_lead_days()`(全局默认 `7`)。按用户养护 lead 刻意**不**建模(M4 的按用户 lead 仅用于 best_before/warranty);养护任务的提前量是*任务*的属性,不是*人*的属性。按用户养护 lead 是已记改进项(§13)。 |
| **外部待办同步(TickTick)是*预留接缝*,不是功能** | M7 **不发**任何 TickTick 代码。它让 `ShoppingListService` 保持**唯一变更咽喉**,让 `NotificationChannel` 协议保持**唯一投递扩展点**,使未来的出站购物同步与未来的"提醒 → TickTick 任务"渠道都是**加法,而非重构**。完整理由、两个接缝、技术现实(OAuth2、无变更 webhook → 轮询、双向对账)见 **§12**;时机决策(在集成里程碑实现)记录在 roadmap §6/§7。 |
| **权限(M6)** | 读(`GET /shopping-list`、`GET /maintenance-schedules`)需 `VIEW`;每个变更(增 / 改 / 勾选 / 入库 / 删 / 清空;计划 增 / 改 / 完成 / 删)需 `EDIT`;均搭乘现有 M6 `require_permission` 扫除。购物清单行记录操作用户,计划(可选)记录。此处没有 `MANAGE_*`。 |
| **错误码(新增)** | 新增:`shopping_list.not_found`(404 —— 操作不存在的清单条目)、`maintenance.not_found`(404 —— 操作不存在的计划)、`validation.unsupported_interval_unit`(422 —— 非法 `interval_unit`,镜像 `validation.unsupported_tracking_mode`)。**三个**新 `ErrorCode`,全部经 M1.5 统一错误信封。勾选入库错误复用现有 `stock.*` 码;实例缺失复用 `instance.not_found`。 |

> 这些扩展 M0–M6 基础。两张新表、每个新 schema、新端点都经现有无漂移契约门(§6)与 M1.5 统一错误信封。**无新运行时依赖**(日历运算用标准库;对账复用 `LowStockService`;提醒来源复用 M4 引擎 + 分发器)。`AGENTS.md` **不变** —— M7 不是"基础"改动。

---

## 3. 数据模型

两张新表 + 养护来源的通知行(复用现有 `notifications` 列 —— **那里无 schema 增量**)。均采用 M0 约定:共享 `Base` 上 SQLAlchemy 2.0 typed `Mapped[...]`,规则在服务层,DB 访问**仅**经仓储。**当前头 = `0032`;M7 加 `0033`–`0034`。**

### 3.1 `shopping_list_items` —— 新表(迁移 `0033`)

持久化、家庭共享的购物清单(自动 + 手动行)。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `source` | String(16) | 否 | `auto`(由低库存物化)/ `manual`(用户录入)。应用层校验对 `SHOPPING_LIST_SOURCES`(无 DB CHECK —— §2.11)。 |
| `definition_id` | FK→item_definitions.id | 是 | `ondelete=CASCADE`。自动行及绑定定义的手动行设置;自由文本手动条目为 **NULL**。 |
| `name` | String(255) | 是 | **无**定义的手动条目的自由文本标签。绑定定义的行,显示名从定义**实时**读取(保持新鲜,不快照)。一行必须有 `definition_id` **或** `name`(应用层交叉校验)。 |
| `desired_quantity` | Numeric(18,6) | 是 | 欲购量(Decimal,绝不 float —— §2.9)。NULL = 未指定(如 level 模式条目,或"买点就行")。 |
| `unit` | String(32) | 是 | 自由文本手动条目的单位(绑定定义的行显示定义的单位)。 |
| `note` | String(1000) | 是 | 自由文本备注。 |
| `purchased_at` | DateTime(tz) | 是 | 勾选状态。NULL = 开放/未勾;勾选时置位。 |
| `created_by` | FK→users.id | 是 | `ondelete=SET NULL`。增加/自动创建该行的用户。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |
| `updated_at` | DateTime(tz) | 否 | `server_default=now()`,更新时刷新。 |

索引:**部分唯一** `uq_shopping_list_one_auto_per_def` 于 `(definition_id) WHERE source='auto'`(**每定义恰好一条自动行,不论已购状态** —— 对账幂等兜底;做成与状态无关意味着勾选/取消勾选往返永不产生冲突的第二条自动行,`sqlite_where` 同 M2 serial 索引);非唯一 `(purchased_at)` 供 开放/已购 拆分。

### 3.2 `maintenance_schedules` —— 新表(迁移 `0034`)

耐用品库存实例上的循环养护任务。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `instance_id` | FK→stock_instances.id | 否 | `ondelete=CASCADE`。该计划养护的耐用品。 |
| `name` | String(255) | 否 | 养护内容(如"更换空调滤网")。 |
| `interval_unit` | String(8) | 否 | `day` / `week` / `month` / `year`。应用层校验对 `MAINTENANCE_INTERVAL_UNITS`(无 DB CHECK —— §2.11)。 |
| `interval_count` | Integer | 否 | `≥ 1`(Pydantic 校验)。"每 `count` 个 `unit`"。 |
| `next_due_date` | Date | 否 | 下次到期日(提醒目标日)。 |
| `lead_days` | Integer | 是 | 提前量覆盖(`≥ 0`)。NULL = 继承全局 `reminders.maintenance.lead_days`。 |
| `last_completed_date` | Date | 是 | 上次完成时间(NULL = 从未)。"标记完成"的重算锚点。 |
| `notes` | String(1000) | 是 | 自由文本。 |
| `is_active` | Boolean | 否 | `server_default=true`。False = 暂停(被引擎跳过,保留供历史)。 |
| `created_by` | FK→users.id | 是 | `ondelete=SET NULL`。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |
| `updated_at` | DateTime(tz) | 否 | `server_default=now()`,更新时刷新。 |

索引:`(instance_id)`(实例详情列表 + 级联)、`(next_due_date)`(引擎"快到期"扫描)、`(is_active)`。

### 3.3 养护通知复用现有 `notifications` 表(无增量)

养护来源写入普通 `notifications` 行 —— M4 列已契合:
- `source = "maintenance"`、`subject_type = "maintenance_schedule"`、`subject_id = schedule.id`(无硬 FK;`subject_*` 按 M4 设计为自由 string/int,删除后作为历史留存)。
- `message_code = "reminder.maintenance"`、`params = {name, instance_name, next_due_date, days_remaining, location_id}`。
- `dedup_key = "maintenance:u{uid}:s{sid}:{next_due_date}"` —— **每(收件人, 计划, 到期日)触发一次**;完成时顺延 `next_due_date` 即得下一次到期的新键(与日期来源去重纪律一致,M4 §4.4)。仅低库存用到的列(`episode_started_on`、`offset_days`、`resolved_at`)保持 **NULL**。

所以 M7 **无需 `notifications` 迁移** —— 只有引擎与服务端消息目录扩展。

### 3.4 迁移列表

| Rev | 步骤 | 做什么 |
|---|---|---|
| `0033` | 1 | 创建 `shopping_list_items`。 |
| `0034` | 4 | 创建 `maintenance_schedules`。 |

两者均可经普通 `op.create_table` / `op.drop_table` 回滚。无数据回填(新表为空;自动行由首次对账创建,而非迁移)。

---

## 4. 后端设计

### 4.1 分层(扩展 M2/M4/M6)

- **常量**(`app/core/stock.py` 或小巧的 `app/core/maintenance.py`):`SHOPPING_LIST_SOURCES = ("auto", "manual")`;`MAINTENANCE_INTERVAL_UNITS = ("day", "week", "month", "year")`。
- **日期助手**(`app/core/dates.py`,新 —— 或扩展现有 core 模块):`add_interval(d: date, unit: str, count: int) -> date`,带**日历正确**的月/年加法 + 月末夹取(§4.4)。纯标准库,重单测。
- **仓储**(`app/repositories/`):
  - `ShoppingListRepository`(新):`create`(捕获唯一索引 `IntegrityError` → 无操作,镜像 `NotificationRepository.create_if_absent`,使并发对账不会抛错)、`get`、`list`(开放 / 含已购,排序)、`update`、`delete`、`get_auto_item(definition_id)`(该定义**任意**状态的自动行 —— 开放或已勾选)、`list_open_auto_items()`(开放未勾选,供剪枝)、`clear_purchased()`。
  - `MaintenanceScheduleRepository`(新):`create`、`get`、`list`(按实例 / 活跃过滤)、`list_for_instance`、`list_active()`(所有 `is_active` 计划,joinedload 实例→定义供路由/命名 —— 引擎在 Python 里施加到期窗口,与 `list_live_with_best_before` 一致;**无标量"地平线"**,因为按计划 `lead_days` 无上界,任何固定地平线都会悄悄漏掉窗口已开的长 lead 计划 —— §4.5)、`update`、`delete`。
  - 仓储之外**无裸查询**(roadmap §2.10)。
- **服务**(`app/services/`):
  - `ShoppingListService`(新)—— **唯一变更咽喉**(TickTick 接缝,§12):`add_manual`、`edit`、`check_off(item_id, intake?)`、`uncheck`、`remove`、`clear_purchased`,及 `reconcile_auto_items()`(§4.3)。带入库的 `check_off` **委托** `StockInstanceService`(不新增库存逻辑)。
  - `MaintenanceScheduleService`(新)—— `create`、`edit`、`delete`、`complete(schedule_id, completed_on?, note?)`(置 `last_completed_date`,经 `add_interval` 顺延 `next_due_date`)、校验(区间单位/数量、实例存在)。
  - `ReminderEngine`(扩展)—— 加法式 `_evaluate_maintenance` pass + `resolve_maintenance_lead`(§4.5)。通知模型不变。
  - **服务端消息目录**(`app/notifications/messages.py`)新增 `reminder.maintenance` 渲染器(ZH+EN),供 email/HTTP/MQTT。

### 4.2 购物清单 CRUD 与勾选

- **加手动**(`POST /shopping-list`,`EDIT`):`{definition_id?, name?, desired_quantity?, unit?, note?}` → 一条 `manual` 行。交叉校验:`definition_id` / `name` 至少其一(否则 `validation.invalid_input`)。`definition_id` 必须存在(`item_definition.not_found`/现有 404)。
- **编辑**(`PATCH /shopping-list/{id}`,`EDIT`):`desired_quantity` / `name` / `note`(经 `model_fields_set` 的 PATCH 语义)。404 `shopping_list.not_found`。
- **勾选**(`POST /shopping-list/{id}/check`,`EDIT`):盖 `purchased_at = now`(自动 + 手动行统一 —— 自动行**不**删除,故仍是其定义的那条唯一自动行,§3.1)。若 body 带 `intake`(`{location_id?, quantity?}`)**且**该行有 `definition_id`:(1) 预检该定义为 `exact`(否则 `stock.movement_not_applicable`);(2) 入库数量解析为 `intake.quantity ?? desired_quantity` —— 若**二者皆 NULL** → `validation.invalid_input`(必须说明买了多少,因自动行不带 `desired_quantity`);(3) 经 `StockInstanceService.create` **为该定义新建一个批次**(记录初始 `intake` 移动,M2 —— 该移动目前按 M2 创建路径记 `user_id=None`;为其归属是既有缺口,§13)。全部在**一个事务**(入库失败回滚勾选)。返回更新后的条目(入库时附创建的实例 id)。
- **取消勾选**(`POST /shopping-list/{id}/uncheck`,`EDIT`):清 `purchased_at`(**不**回滚任何入库 —— 那是独立的库存操作;已写明)。对自动行安全,因为每定义自动行的唯一性**与状态无关**(§3.1),清 `purchased_at` 永不与第二条自动行冲突。缺失则 404。
- **删除**(`DELETE /shopping-list/{id}`,`EDIT`):硬删。缺失则 404。
- **清空已购**(`POST /shopping-list/clear-purchased`,`EDIT`):删除所有 `purchased_at IS NOT NULL` 行;返回删除数。
- **刷新**(`POST /shopping-list/refresh`,`EDIT`):强制 `reconcile_auto_items()` 并返回当前清单(无需扫描即可在 UI 演示自动填充)。

### 4.3 自动对账(易错的幂等 —— 必测)

```
reconcile_auto_items():
    if not settings.shopping_list_auto_add():      # 门:shopping_list.auto_add_low_stock(默认 true)
        return
    low = LowStockService(db).compute()            # 复用 M2/M4 信号 —— 绝不重新推导规则
    low_def_ids = { item.definition_id for item in low }

    # 1. 开:为每个当前低的定义确保唯一一条开放自动行
    for item in low:
        existing = repo.get_auto_item(item.definition_id)
        if existing is None:
            repo.create(source='auto', definition_id=item.definition_id,
                        desired_quantity=None, created_by=None)   # create 捕获 IntegrityError -> 无操作(DB 兜底)
        elif existing.purchased_at is not None:
            repo.clear_purchased_at(existing)      # 勾选后未补货便再次掉低:重开(保底行为)

    # 2. 剪:删除其定义已恢复的、开放且未勾选的自动行
    for row in repo.list_open_auto_items():
        if row.definition_id not in low_def_ids:
            repo.delete(row)
```

- "开放" = `purchased_at IS NULL`。**已勾选**且其定义**仍低**的自动行会被**重开**(`purchased_at` 清空),使建议重新浮现,无需用户先"清空已购"。真实补货使库存高于 `min_stock`,故已真正恢复的定义不会为低,其已勾选行不会被重开。
- 有意后果:若用户勾选了一条自动行却没有真正补货(定义仍低于 `min_stock`),下次对账/刷新会重开该行。这是有意的保底行为。
- 部分唯一索引 `(definition_id) WHERE source='auto'` 使"**每定义恰好一条自动行(任意状态)**"成为 DB 不变量;`create` 捕获 `IntegrityError` 并无操作,故重跑对账 —— 或任何勾选/取消勾选往返 —— 都不会产生重复(这正是堵住取消勾选冲突洞的 B 类修复)。
- **恢复会剪掉建议**(回到阈值之上的*开放未勾选*自动行消失)—— 但绝不删手动行;已勾选且定义已恢复的行留在"已购"区,直到"清空已购"。
- 由**每日扫描**、**按需 `POST /reminders/run`**、`consume_fifo` / `discard` / `adjust` 三者中的**低库存事件钩子**(尽力而为 + savepoint 隔离;可复用钩子已跑出的 `LowStockService.compute()` 结果)、**`POST /refresh`** 调用 —— 均幂等。

### 4.4 `add_interval` —— 日历正确的循环(必测)

```
add_interval(d, unit, count):
    if unit == 'day':   return d + timedelta(days=count)
    if unit == 'week':  return d + timedelta(weeks=count)
    if unit == 'month': return _add_months(d, count)
    if unit == 'year':  return _add_months(d, count * 12)

_add_months(d, n):
    m = d.month - 1 + n
    year  = d.year + m // 12
    month = m % 12 + 1
    day   = min(d.day, _days_in_month(year, month))   # 月末夹取
    return date(year, month, day)
```

- **夹取**是陷阱:1-31 + 1 月 → 2-28(闰年 2-29);8-31 + 6 月 → 2-28/29。跨闰年与各月边界测试。
- 仅标准库(`calendar.monthrange` 求 `_days_in_month`);**无 `dateutil`** 依赖。

### 4.5 养护提醒来源(加法式引擎 pass)

`ReminderEngine` 中新增 `_evaluate_maintenance(active_users, active_by_id, today_local)` pass,在日期来源与低库存之后由 `run_scan()` 调用(现有来源**不**改动):

```
schedules = MaintenanceScheduleRepository(db).list_active()
                # 所有活跃计划,joinedload 实例→定义;下面的到期窗口在 Python 里施加
                #(与 list_live_with_best_before 一致)。无标量 horizon:按计划 lead_days
                # 无上界,任何固定 horizon 都会漏掉窗口已开的长 lead 计划。
for s in schedules:
    lead   = s.lead_days if s.lead_days is not None else settings.maintenance_lead_days()
    window = s.next_due_date - timedelta(days=lead)
    if today_local < window:
        continue
    recipients = _recipients_for(_effective_responsible_for_lot(s.instance), active_users, active_by_id)
    for u in recipients:
        if not u.notify_in_app and not u.notify_email_digest:   # M6 偏好门
            continue
        dedup = f"maintenance:u{u.id}:s{s.id}:{s.next_due_date.isoformat()}"
        params = { "name": s.name,
                   "instance_name": s.instance.definition.name,   # 耐用品的产品名
                   "next_due_date": s.next_due_date.isoformat(),
                   "days_remaining": (s.next_due_date - today_local).days,
                   "location_id": s.instance.location_id }
        notification, created = notification_repo.create_if_absent(
            user_id=u.id, source="maintenance",
            subject_type="maintenance_schedule", subject_id=s.id,
            dedup_key=dedup, message_code="reminder.maintenance", params=params)
        # 像其它来源一样计数 + 收集新行
```

- **路由**逐字复用 M6:耐用品实例的 `responsible_user_id` → 其定义的 → 回退全体活跃用户。实例 + 定义经 joinedload,无 N+1。
- **去重**每次到期触发一次;**完成顺延 `next_due_date`**,故下次到期得新键。直接编辑 `next_due_date` 同样得到新一次到期(正确)。
- **lead** 按计划 → 全局;lead `0` 当天触发;**逾期**计划(今天已过 `next_due_date`)仍触发(窗口已过),`days_remaining` 转负(渲染显示"逾期")。
- `ScanSummary.maintenance` 与 `ReminderRunSummary.maintenance` 携带创建计数;新行搭乘现有**提交后分发**(站内 + 邮件摘要 + HTTP + MQTT),用新的 `reminder.maintenance` 服务端目录文案。**无事件触发路径**(养护日期越过窗口只在日期翻天发生,而那由每日扫描负责 —— 与日期来源同理,M4 §2)。

### 4.6 设置新增

向 `SettingsService` 加两个代码定义默认(表只存覆盖 —— M4 §2):
- `reminders.maintenance.lead_days` → 默认 `7`(Pydantic `ge=0`)。
- `shopping_list.auto_add_low_stock` → 默认 `true`。

在 `SettingsResponse`/`SettingsUpdate` 组(reminders 段新增 `maintenance_lead_days`;一个小 `shopping_list` 段带 `auto_add_low_stock`)及配置页(§7)呈现。

### 4.7 API 面(增量;均在 `settings.api_prefix` 下,默认 `/api`)

| 方法 + 路径 | 权限 | 用途 |
|---|---|---|
| `GET /shopping-list?include_purchased=` | `VIEW` | 列出条目(开放在前;解析后的名/单位/数量),各标 `source`。 |
| `POST /shopping-list` | `EDIT` | 加手动条目(`definition_id?`/`name?` + 数量/单位/备注)。 |
| `PATCH /shopping-list/{id}` | `EDIT` | 改数量/名/备注。404 `shopping_list.not_found`。 |
| `POST /shopping-list/{id}/check` | `EDIT` | 标记已购;可选 `{intake}` → 经 M2 新建批次。 |
| `POST /shopping-list/{id}/uncheck` | `EDIT` | 撤销已购。 |
| `DELETE /shopping-list/{id}` | `EDIT` | 删除条目。 |
| `POST /shopping-list/clear-purchased` | `EDIT` | 删除所有已勾条目;返回数量。 |
| `POST /shopping-list/refresh` | `EDIT` | 强制自动对账;返回当前清单。 |
| `GET /maintenance-schedules?instance_id=&active=` | `VIEW` | 列出计划(可限定/过滤)。 |
| `GET /instances/{id}/maintenance-schedules` | `VIEW` | 某耐用品的计划(实例详情视图)。 |
| `POST /maintenance-schedules` | `EDIT` | 创建 `{instance_id, name, interval_unit, interval_count, next_due_date, lead_days?, notes?}`。 |
| `GET /maintenance-schedules/{id}` | `VIEW` | 单个计划。404 `maintenance.not_found`。 |
| `PATCH /maintenance-schedules/{id}` | `EDIT` | 编辑(含 `is_active`、区间、`next_due_date`、`lead_days`)。 |
| `DELETE /maintenance-schedules/{id}` | `EDIT` | 删除。 |
| `POST /maintenance-schedules/{id}/complete` | `EDIT` | `{completed_on?, note?}` → 记录完成,顺延 `next_due_date`。 |
| `GET/PATCH /settings` | `VIEW` / `MANAGE_SETTINGS` | 现在还携带 `maintenance_lead_days` + `shopping_list.auto_add_low_stock`。 |
| `POST /reminders/run` | `MANAGE_SETTINGS` | 摘要现在还含 **`maintenance`** 计数。 |

### 4.8 Schema(`app/schemas/`)

- `ShoppingListItemResponse`(`id, source, definition_id, name(已解析), desired_quantity, unit, note, purchased_at, created_at`);`ShoppingListItemCreate`(`definition_id?`、`name?`、`desired_quantity?`、`unit?`、`note?`);`ShoppingListItemUpdate`(PATCH);`ShoppingListCheck`(`intake?: ShoppingListIntake{location_id?, quantity?}` —— **仅** M2 创建路径会贯穿的字段;`occurred_at`/`note` 刻意缺席,因 `StockInstanceService.create` 不接受它们,§13);`ShoppingListCheckResponse`(`item`、`created_instance_id?`)。
- `MaintenanceScheduleResponse`(全部列 + 解析后的 `instance_name`、**服务端计算的** `status: overdue|due_soon|ok`、以及**解析后的 `effective_lead_days`** = `lead_days ?? 全局默认`,使客户端无需知道全局默认即可渲染状态片);`MaintenanceScheduleCreate`;`MaintenanceScheduleUpdate`(PATCH);`MaintenanceComplete`(`completed_on?`、`note?`)。校验:`interval_count ge=1`、`lead_days ge=0`、`interval_unit` 对 `MAINTENANCE_INTERVAL_UNITS`(→ `validation.unsupported_interval_unit`)。
- `ReminderRunSummary` 新增 `maintenance: int`。`SettingsResponse`/`SettingsUpdate` 新增 `maintenance_lead_days`(reminders)+ 一个 `shopping_list` 段(`auto_add_low_stock`)。
- 所有数量在线为 Decimal-字符串(roadmap §2.9);日期 ISO。

---

## 5. 质量门与易错逻辑(完成定义)

继承 M0–M6 §5(`make check` 全绿;构建;`make codegen` 无漂移)。M7 中**必须有单测的逻辑**(roadmap DoD —— *日期计算、阈值触发、幂等、库存进出*):

**后端**
- **`add_interval` 日历运算**(§4.4 —— 必测):天/周加;月/年加;**月末夹取**(1-31 +1月 → 2-28/29;8-31 +6月 → 2-28/29);闰年二月;跨年;`count ≥ 2`;年 = ×12 月。
- **购物清单对账幂等**(§4.3 —— 必测):无自动行的低定义 → 创建一条;**重跑不创建**(幂等 / 与状态无关的部分唯一);已恢复定义的**开放未勾选**自动行被**剪掉**;**手动**行绝不被自动剪;**已勾选且其定义仍低**的自动行被**重开**(`purchased_at` 清空,建议重新浮现);**已勾选且其定义已恢复**的自动行留在"已购"区(不剪);对一条自动行**勾选 → 取消勾选往返永不冲突**(B 类回归 —— 与状态无关的每定义唯一性);`auto_add_low_stock=false` 门使对账无操作;level 模式低条目得到一条 NULL 数量的自动行。
- **勾选 → 入库**(§4.2):不带入库的勾选只盖 `purchased_at`;**带**入库的勾选经现有服务建批次且数量**由账本派生**(存在 `intake` 移动;`quantity == SUM(deltas)`);入库数量 = `intake.quantity ?? desired_quantity`,**二者皆 NULL → `validation.invalid_input`**;对非 `exact` 定义入库 → `stock.movement_not_applicable`(`check_off` 模式预检);整操作原子(入库失败回滚勾选);入库后该定义(已高于 min)在下次对账**从清单消失**。
- **养护完成 → 顺延**(§4.5/§4.4):完成置 `last_completed_date` 并顺延 `next_due_date = add_interval(completed_on, unit, count)`;回填 `completed_on` 从该日顺延;顺延后的日期得新去重命名空间。
- **养护提醒触发与路由**(§4.5 —— 必测):恰在 `today_local ≥ next_due_date − lead` 触发,**边界**(`== window` 触发,`window − 1` 不触发);**逾期**以负 `days_remaining` 触发;**按计划 lead 胜过全局**;lead `0` 当天触发;**`next_due_date` 很远但窗口已开的长 lead 计划仍触发**(全活跃候选查询 —— 无 horizon 漏掉它,B 类修复);**暂停**(`is_active=false`)计划从不触发;重跑扫描不创建(去重);路由 = 实例→定义→回退全员(复用 M6 助手)并遵守偏好门;**三个现有来源不受影响**(回归测试断言有养护在场时 best_before/warranty/低库存计数不变)。
- **设置**:`maintenance_lead_days` 默认 `7`、`auto_add_low_stock` 默认 `true`;两者往返;`ge=0` 校验;`ReminderRunSummary.maintenance` 返回。
- **权限**(M6):`viewer` 被每个购物清单 / 养护变更拒绝(403 `auth.forbidden`);`member`/`admin` 允许;读对全员开放。
- **迁移往返**:`0033`–`0034` 在 `0032` 库上干净升级并干净降级;现有行不受影响。

**前端**(vitest + Testing Library,mock typed client —— M0 风格):**购物清单页**列出自动 + 手动行(source 徽标),加手动条目,改数量,勾选(带可选**入库**弹窗,复用现有入库表单),清空已购,刷新;一次**低库存**掉落在刷新后浮现自动行;耐用品详情上的**养护区块**列出计划及 due/overdue/ok 状态,增/改/删,"标记完成"(下次到期顺延);**仪表盘即将养护磁贴**渲染计数 + 最近条目;**配置页**编辑 `maintenance_lead_days` + `auto_add_low_stock`;**通知铃铛**从 `message_code`+`params` 本地化渲染 `reminder.maintenance`;所有新字符串在 **en + zh** 齐全;日期/数字经 M1.5 `formatDate`/`formatQuantity`。

---

## 6. 契约优先代码生成与无漂移门

机制不变(M0–M6 §6)。每个触及 API 的步骤**重跑 `make codegen`** 并提交 `openapi.json` + `frontend/src/api/schema.d.ts`;CI **contract** job 在漂移时失败。M7 用购物清单 + 养护路径与 schema、`ReminderRunSummary` 上的 `maintenance` 计数、两个新设置字段扩展 schema。**不**触及 schema 的步骤(如对账事件钩子接线、`add_interval` 助手)在其步骤注记中写明。

---

## 7. 前端设计

### 7.1 购物清单页(`pages/ShoppingList.tsx`,新)+ 路由 + 导航
- 单一清单,**开放条目在前**,然后一个可折叠的"已购"区。每行显示 **source**(低库存派生行带"auto"徽标 vs 普通手动行)、解析后的名(从定义实时读取,或自由文本标签)、欲购量 + 单位、备注。
- 动作:**加手动条目**(小表单/弹窗:选定义*或*输自由文本名,+ 数量/单位/备注)、内联**改数量/备注**、**勾选**(复选框;打开可选的**"加入库存"**入库步骤 —— 复用现有 M2 入库表单,预填定义 + 欲购量 + 位置选择 —— 或"仅勾选"不入库)、**取消勾选**、**删除**、一个**"清空已购"**按钮、一个**"刷新"**按钮(`POST /refresh`,供演示 / 手动同步)。
- 既无低库存又无手动条目时的空状态。
- 新 `shoppingList` 命名空间(en + zh);一个导航项(购物车/清单图标)。

### 7.2 养护计划 —— 耐用品详情区块(`pages/InstanceDetail.tsx`)
- 在**耐用品**实例详情页,一个**"养护"**区块列出其计划:名称、循环("每 3 个月")、`next_due_date`、一个从服务端计算的 `status` 字段渲染的**状态片**(`overdue` / `due_soon` / `ok` —— §4.8,故客户端无需知道全局 lead 默认)、`last_completed_date`。
- 动作:**加计划**(名称、区间单位 + 数量、首个 `next_due_date`、可选 lead、备注)、**编辑**、**暂停/恢复**(`is_active`)、**删除**、**"标记完成"**(可回填 `completed_on` + 备注 → 下次到期顺延,状态片更新)。
- 新 `maintenance` 命名空间(en + zh)。

### 7.3 仪表盘即将养护磁贴(`pages/Dashboard.tsx`)
- 一个镜像现有 过期 / 低库存 磁贴的磁贴:**在其 lead 窗口内(含逾期)到期**的计划计数 + 一个最近优先短列表(实例 + 任务 + 到期日),链接到耐用品。(读取 `GET /maintenance-schedules?active=true` 并保留服务端 `status` 为 `overdue`/`due_soon` 的行,按最近优先排序 —— 无需额外查询参数。)无到期时空状态。

### 7.4 配置页新增(`pages/Configuration.tsx`)
- Reminders 段新增一个**养护 lead 天数** NumberInput(min 0)。一个小 **购物清单** 段新增一个**"自动加入低库存条目"**开关。均经 `GET/PATCH /settings`。

### 7.5 通知渲染(`notifications` 命名空间)
- 新增 **`reminder.maintenance`** 模板(en + zh)带插值(`{name}`、`{instance_name}`、`{next_due_date}`、`{days_remaining}`;`days_remaining < 0` 时一个"逾期"变体),使铃铛/收件箱与 `/notifications` 页渲染新来源。服务端目录(`messages.py`)携带对应 ZH+EN 文案供 email/HTTP/MQTT。

### 7.6 i18n
新命名空间 **`shoppingList`**、**`maintenance`**(en + zh),注册于 `src/i18n/index.ts`;并向 `nav`(购物清单)、`dashboard`(养护磁贴)、`configuration`(养护 lead + 自动加)、`notifications`(`reminder.maintenance`)、`errors`(三个新码)补充。测试钉在 `en`(M1.5)。

---

## 8. CI 与 Docker(相对 M6 的增量)

- **无新运行时依赖。** 日历运算是标准库(`calendar`、`datetime`);对账复用 `LowStockService`;养护来源复用 M4 引擎 + 分发器 + 服务端目录。`AGENTS.md` 依赖行**不变**。
- **Docker**:**migrate** 冒烟步骤现在应用 `0001`–`0034`。单镜像 + 单绑定挂载不变。一切新内容零配置:全新部署仍引导单管理员(M0);购物清单初始为空,仅当某物掉低才自动填充;养护在加入计划前为空;`auto_add_low_stock` 默认开,`maintenance_lead_days` 默认 7。
- 其余(契约门、缓存、fail-closed `migrate` 服务、`environment=test` 下调度器/MQTT 关)与 M4 §8 / M6 §8 一致。

---

## 9. 步骤拆分(原子、有序)

每步独立可测、有测试背书、落且仅落一个提交(编排模式下一次按步 autosquash),触及 API 则重跑 `make codegen`,并继承全局 DoD(§5)+ **反夹带规则**:只实现当前步骤 —— 不做其它步骤、不顺手重构、不碰宿主真实环境(测试沙箱外不碰真实 DB/容器/文件)。

> **分阶段:** A(购物清单后端)→ B(养护后端 + 提醒来源)→ C(前端)。后端 A–B 稳定契约;前端 C 消费它。C 各步依赖其后端对应步。

### 阶段 A — 购物清单(后端)

**步骤 1 — `shopping_list_items` 表 + CRUD 服务 + 端点**
- **构建:** 迁移 `0033`;`ShoppingListItem` 模型(+ 部分唯一开放-自动索引);`ShoppingListRepository`;`ShoppingListService` CRUD(`add_manual`、`edit`、**不带**入库的 `check_off`、`uncheck`、`remove`、`clear_purchased`);schema;路由(`GET`/`POST`/`PATCH`/`DELETE /shopping-list`、`/{id}/check`、`/{id}/uncheck`、`/clear-purchased`);错误码 `shopping_list.not_found`;M6 `EDIT`/`VIEW` 门;`make codegen`。
- **测试:** 加手动(绑定定义 + 自由文本);名/定义交叉校验;编辑;勾选/取消盖 `purchased_at`;清空已购;404;权限门;迁移升/降。
- **提交:** `feat(backend): shopping list table, CRUD service and endpoints`

**步骤 2 — 由低库存自动对账 + 设置 + 事件钩子 + 刷新**
- **构建:** `reconcile_auto_items()` 复用 `LowStockService`;`shopping_list.auto_add_low_stock` 设置(默认 true)入 `SettingsService` + schema;把对账接入**每日扫描**、**`POST /reminders/run`**、`StockMovementService` 中的**低库存事件钩子**(`evaluate_low_stock` 旁);`POST /shopping-list/refresh`;`make codegen`(仅设置字段)。
- **测试(必测):** 每个低定义开一行;重跑幂等(部分唯一);恢复剪掉开放未勾选自动行;手动 + 已勾选行不动;门关 → 无操作;level 模式低 → NULL 数量自动行;刷新返回对账后清单。
- **提交:** `feat(backend): auto-populate shopping list from low stock`

**步骤 3 — 勾选 → 入库(委托 M2 账本)**
- **构建:** `ShoppingListService.check_off(item_id, intake)` 委托 `StockInstanceService.create`(新批次,初始 `intake`);`ShoppingListCheck`/`ShoppingListCheckResponse` schema;扩展 `POST /shopping-list/{id}/check` 接受 `intake`;`make codegen`。
- **测试:** 入库建账本支撑的批次(`quantity == SUM(deltas)`);数量默认取 `desired_quantity`;非 `exact` → `stock.movement_not_applicable`;入库失败原子回滚;入库后对账剪掉恢复行。
- **提交:** `feat(backend): shopping-list check-off with stock intake`

### 阶段 B — 养护计划 + 提醒来源(后端)

**步骤 4 — `maintenance_schedules` 表 + `add_interval` + CRUD/完成服务 + 端点**
- **构建:** 迁移 `0034`;`MaintenanceSchedule` 模型;`MAINTENANCE_INTERVAL_UNITS` 常量;`add_interval` 日期助手(日历正确,标准库);`MaintenanceScheduleRepository`;`MaintenanceScheduleService`(create/edit/delete/`complete` 带顺延);schema;路由(`GET`/`POST`/`PATCH`/`DELETE /maintenance-schedules`、`/{id}/complete`、`GET /instances/{id}/maintenance-schedules`);错误码 `maintenance.not_found`、`validation.unsupported_interval_unit`;`EDIT`/`VIEW` 门;`make codegen`。
- **测试(必测):** `add_interval` 矩阵含月末夹取 + 闰年;create/edit/delete;complete 置 `last_completed_date` + 顺延 `next_due_date`;回填完成;非法区间单位/数量被拒;按实例列表;404;权限门;迁移升/降。
- **提交:** `feat(backend): maintenance schedules with calendar recurrence`

**步骤 5 — 养护提醒来源(加法式引擎 pass)**
- **构建:** `ReminderEngine` 中 `_evaluate_maintenance` pass + `resolve_maintenance_lead`(经 `_effective_responsible_for_lot(schedule.instance)` 路由,按到期去重,偏好门);`reminders.maintenance.lead_days` 设置(默认 7);服务端目录(`messages.py`,ZH+EN)中的 `reminder.maintenance`;`ScanSummary.maintenance` + `ReminderRunSummary.maintenance`;分发不变(搭乘提交后)。`make codegen`(摘要字段)。
- **测试(必测):** 在 `today ≥ due − lead` 触发(+ 边界);逾期触发(负天数);按计划 lead 胜过全局;lead 0;暂停从不触发;重扫去重;路由 实例→定义→回退 + 偏好门;**三个现有来源不变**(回归);email/MQTT/HTTP 渲染养护行。
- **提交:** `feat(backend): maintenance-due reminder source`

### 阶段 C — 前端

**步骤 6 — 购物清单页**
- **构建:** `pages/ShoppingList.tsx` + 路由 + 导航(列表、source 徽标、加手动、编辑、带可选入库弹窗(复用 M2 入库表单)的勾选、取消勾选、删除、清空已购、刷新);`shoppingList` 命名空间(en+zh);`errors` 中的 `shopping_list.not_found`。
- **测试:** 经 typed client 列出/加/改/勾选(+入库)/清空/刷新;刷新后浮现自动行;en+zh 齐全。
- **提交:** `feat(frontend): shopping list page`

**步骤 7 — 养护 UI(耐用品区块 + 仪表盘磁贴)**
- **构建:** `InstanceDetail` 上的**养护**区块(列表 + 状态片 + 加/改/暂停/删/标记完成)与**仪表盘即将养护磁贴**;`maintenance` 命名空间(en+zh);`errors` 中的 `maintenance.not_found` + `validation.unsupported_interval_unit`。
- **测试:** 区块列表 + 状态;create/edit/delete/标记完成(下次到期顺延);仪表盘磁贴计数 + 列表;en+zh 齐全。
- **提交:** `feat(frontend): maintenance schedules UI and dashboard tile`

**步骤 8 — 配置 + 通知渲染**
- **构建:** 配置页新增(`maintenance_lead_days` + `auto_add_low_stock`);`notifications` 命名空间中的 `reminder.maintenance` 模板(含逾期变体)(en+zh);确认铃铛/收件箱/`/notifications` 渲染新来源。
- **测试:** 两字段的设置 加载/编辑/PATCH;铃铛渲染 `reminder.maintenance`(正常 + 逾期);en+zh 齐全。
- **提交:** `feat(frontend): maintenance settings and notification rendering`

> 步骤过大可拆(如步骤 4 迁移-vs-服务、步骤 6 列表-vs-入库弹窗);各自保持独立绿。阶段内后端步骤大体独立,除 2→1 与 3→1;C 步骤依赖其对应步(6→1/2/3,7→4/5,8→5)。

---

## 10. 盲评检查点(逐步)

评审者**仅**得到:本文 + roadmap、该步实现简报、该步 diff。检查:

- **步骤 1:** `source` **应用层**校验(无 DB CHECK);开放-自动**部分唯一**索引在位;名/定义交叉校验;`shopping_list.not_found`;变更上 `EDIT` / 读上 `VIEW`;codegen 已提交。
- **步骤 2:** 对账**复用 `LowStockService`**(不重新推导低库存规则);幂等;每定义自动行唯一性**与状态无关**(`WHERE source='auto'`,而非 `… AND purchased_at IS NULL`),故勾选/取消勾选不会产生冲突的第二条自动行,且 `create` 捕获 `IntegrityError`;恢复**仅**剪掉开放未勾选自动行(绝不动手动、绝不动已勾选);由 `auto_add_low_stock` 控制;接入**全部三条**移动路径(consume/discard/adjust)**尽力而为 + savepoint 隔离**;**引擎保持解耦**(调用方调对账,引擎不 import 购物清单);codegen 已提交。
- **步骤 3:** 入库**委托现有 `StockInstanceService`**(无新数量计算 —— roadmap §2.3);数量由账本派生;数量 = `intake.quantity ?? desired_quantity`(二者皆 NULL → `validation.invalid_input`);非 `exact` 经一个**模式预检**以现有 `stock.movement_not_applicable` 拒绝(而非 `instance.field_mode_mismatch`);`ShoppingListIntake` 仅暴露 `{location_id?, quantity?}`;单事务(原子);codegen 已提交。
- **步骤 4:** `add_interval` 日历正确含**月末夹取**(标准库,无 `dateutil`);`interval_unit` 应用层校验;`complete` 从 `completed_on` 顺延;`maintenance.not_found` / `validation.unsupported_interval_unit`;迁移可逆;codegen 已提交。
- **步骤 5:** 养护是**加法式** pass —— 两个 `_DateSource` 评估器与低库存 pass **不动**;候选查询返回**所有活跃**计划,`today ≥ due − lead` 窗口在 Python 里施加(无可能漏掉长 lead 计划的标量 horizon);逾期触发;经 `_effective_responsible_for_lot(schedule.instance)` 路由 + 回退 + 偏好门(M6 一致);每到期去重一次;暂停跳过;`messages.py` 带 ZH+EN;三个现有来源计数不变(回归);codegen 已提交(仅摘要字段)。
- **步骤 6:** 列表读 `GET /shopping-list`(无客户端重新推导);勾选入库复用**现有**入库表单;仅 typed client;en+zh 齐全。
- **步骤 7:** 计划状态按 lead 计算;标记完成往返并顺延;仪表盘磁贴镜像 过期/低库存 磁贴模式;en+zh 齐全。
- **步骤 8:** 设置往返;`reminder.maintenance` 从 **`message_code`+`params`** 本地化(站内不渲染服务端文案);逾期变体;en+zh 齐全。
- **横切:** 符合 roadmap **§2.3(由账本派生 / 绝不覆盖 —— 勾选入库走账本)**、**§2.6(一个引擎、多来源 —— 养护是加法,不是分叉)**、**§1.2(单一家庭、共享清单)**、§2.10(单一上下文/仓储层)、§2.11(逻辑在应用层,`source`/`interval_unit` 无 DB CHECK);M1.5 统一错误信封(仅三个新码,无裸 `detail`);**TickTick 接缝保持预留、不实现**(§12);本文变更时遵守**双语文档规则**。

---

## 11. 🟢 部署自测点(并入 M7 里程碑走查)

里程碑末作者运行的手动走查(展开 roadmap M7 🟢 项)。假设 M1–M6 运行流(compose up;以管理员登录;存在位置树、若干带 `min_stock` 的消耗品、一个有负责人的耐用品)。

1. **由低库存自动填充:** 把某消耗品消耗到 `min_stock` 以下。打开**购物清单**(或点**刷新**)→ 该条目以**自动**行出现。(经事件钩子,它在消耗掉低的那一刻也已出现。)
2. **手动条目:** 手动加一条自由文本条目("纸巾",数量 2)→ 它以手动行出现。
3. **勾选 → 入库:** 勾选自动消耗品并选**加入库存**,把已购量入库到某位置 → 一条真实 `intake` 移动落地(批次数量由账本派生);因为该定义现已高于 `min_stock`,自动行在下次刷新/扫描**消失**。勾选手动条目并**清空已购**。
4. **创建养护计划:** 在某耐用品详情页加一个计划("更换空调滤网",每 **3 个月**,首次到期在未来几天内,lead 7)→ 它以 **`due_soon`** 状态片列出;**仪表盘即将养护磁贴**显示它。
5. **养护提醒触发:** **立即运行扫描** → 一条**养护提醒**出现在铃铛/收件箱,路由到耐用品的**负责人**(把耐用品指派给某成员,确认仅他收到;未指派的耐用品到达全员)。配了 email/MQTT/HTTP 时,摘要 / 状态 / webhook 也带养护行。重跑 → **无重复**。
6. **标记完成 → 顺延:** 对计划**标记完成** → 置 `last_completed_date`,`next_due_date` 按周期向前滚(日历正确 —— 月末情形会夹取),状态片回到 **ok**,且**不触发新一次到期**(下次到期是新日期)。已投递的收件箱通知会留到你读它为止 —— 养护与其它日期来源一样没有自动关闭。
7. **暂停:** 暂停一个计划(`is_active=false`)→ 停止触发;恢复 → 再次触发。
8. **设置:** 在**配置**页改**养护 lead**并切换**自动加入低库存**;确认行为随之变化。
9. **角色:** 以 **viewer** 确认购物清单与养护**只读**(无 加/勾选/标记完成);以 **member** 确认可全用。
10. **CI 绿:** 同一套门在 GitHub Actions 通过,含无漂移契约门;迁移 `0001`–`0034` 在 docker 冒烟里于全新库干净应用。

---

## 12. 预留集成接缝 —— 外部待办 / 购物同步(TickTick)

> **M7 不构建其中任何一部分。** 本节记录*为何现在预留接缝*与*未来实现挂在何处*,使后续集成是**加法,而非重构**。**时机决策** —— 在集成里程碑家族(M9 一带)实现,而非 M7 —— 记录在 roadmap §6(停车场)与 §7(开放问题)。

**为何现在预留、稍后实现。** TickTick 式同步是一个**外部 SaaS、OAuth2、双向**集成 —— 恰是集成里程碑(M9:公共 REST API + 通用 webhook + OAuth/token 基础设施)的主题,而 M9 本就要搭 token 存储 + OAuth 管道。把它塞进 M7 会让一个核心领域回路耦合于第三方 API 的稳定性,并撑爆一个旨在*闭合回路*的里程碑。项目既定纪律是"**现在预留抽象,稍后实现**"(roadmap §1.3 / §2.12 —— LLM 后端即先例)。MQTT/HA 桥之所以**提前**进 M4,是因为它是提醒差异化的核心;一个外部待办镜像不是 —— 它是锦上添花。

**未来实现挂靠的两个接缝(M7 后均已存在 —— 按设计,零额外成本):**

1. **出站购物清单同步。** `ShoppingListService` 是每次清单变更(加 / 改 / 勾选 / 取消 / 删 / 对账)的**唯一变更咽喉**。未来一个 `ShoppingListSyncProvider`(默认无操作)观察这些变更并镜像到一个 TickTick **项目/清单**(并在入站轮询时,把在 TickTick 勾掉的条目标为已购)。自然的未来新增是 `shopping_list_items` 上一个可空 **`external_ref`** 列做 id 映射 —— **已记,现在不加**(同步不上线前不为它买单)。
2. **提醒 → 任务。** 未来一个 **`TickTickChannel`** 实现**现有 `NotificationChannel` 协议**(M4 分发器已可插拔:站内 / email / HTTP / MQTT)。它会为每条新提醒创建一个带到期日的 TickTick **任务** —— "某易腐品将过期 / 某保修将到期 / **养护到期** → 你每天都看的待办上多一条"。这把作者"将过期 → 加进 TickTick"的意图**直接落到渠道接缝**上 —— 又因为每条 M7/M4 通知已携带结构化 `{message_code, params}`,任务标题/正文/到期日**无需新服务端文案**即可渲染。

**未来实现要处理的技术现实(推迟 —— 均归 M9 OAuth/token 工作):**
- **OAuth2** 授权码流 + **token 存储与刷新**(按家庭,或在账号与 TickTick 账号 1:1 时按用户)—— 复用 M9 凭据基础设施;**不**另造平行密钥库。
- **入站同步很可能需轮询。** TickTick Open API 暴露任务/项目,但(据现有了解)**无变更 webhook**,故"在 TickTick 勾掉 → 这里标已购"必须按间隔**轮询** —— 设计时核实确切 API 能力。
- **双向对账** —— 冲突解决(两处都勾)、去重、**幂等**(别每次扫描重建任务;按 `external_ref` 映射)。这一分布式同步面正是它*不*属于 M7 的原因。
- **范围开关** —— 让每个方向独立可选(出站购物镜像;提醒→任务渠道),默认关,与其它 M4 渠道一致。

**M7 为压低接缝成本刻意所做:** 让 `ShoppingListService` 保持唯一变更路径(没有路由直接写仓储);让 `NotificationChannel` 协议保持唯一投递扩展点;为新养护来源像其它来源一样发出结构化 `{message_code, params}`。不发任何 TickTick 专有内容,后续也无需返工 M7。

---

## 13. 开放问题 / 推迟

- **基于用量/里程表的养护**("每 10 000 公里 / 200 运行小时"):M7 仅日历。一个用量表概念(你记录的读数、以表单位计的区间)是受控的后续新增 —— 它需要 M7 没有的表模型。
- **养护完成历史:** M7 只保留 `last_completed_date`。一个逐次完成账本(日期、备注、费用、谁)—— M2 库存账本的耐用品养护类比 —— 若真实使用需要历史/审计,是自然的跟进。
- **按定义的养护计划:** M7 挂在具体实例。一个定义级"适用于每个单元"的计划(镜像 M6 负责人"定义与实例两边都挂"形态)推迟到真实的多单元同产品场景出现。
- **按用户养护 lead / 按计划循环的丰富度:** M7 lead 按计划 → 全局(无按用户)。"每月第 n 个星期几"、"跳过周末"、类 cron 循环属停车场。
- **购物清单人体工学:** 一份共享清单,无按店货架分组、无多个命名清单、无"把购物指派给某人"。在单清单证明受限前都属停车场(roadmap §1.2 维持一个家庭一份)。
- **自动行数量建议:** 自动行 `desired_quantity` 为 NULL。建议补货量(如 `min_stock − current`,或按定义的补货量)是补货点 UX 稳定后的改进(关联 M2 §12 `min_stock` 语义)。
- **勾选入库目标:** M7 勾选入库**新建批次**。补到**现有**批次,或选批次,是后续选项(M2 已暴露按批 `intake`)。
- **勾选入库归属 + 字段:** 勾选时创建的初始入库移动记 `user_id=None`(`StockInstanceService.create` 既有限制,**非** M7 引入 —— 购物清单行本身记 `created_by`);且 `ShoppingListIntake` 省去 `occurred_at`/`note`,因 M2 创建路径不贯穿它们。把操作用户 + 回填/备注贯穿创建路径是个小跟进。
- **清空已购前再次掉低(已修复):** 若某定义在其自动行仍**已勾选**(留在"已购"区)时恢复、随后又掉低,对账现在会**重开该已勾选行**(`purchased_at` 清空),使建议立刻重新浮现 —— 无需用户先"清空已购"。有意后果:勾选自动行而未真正补货(定义仍低于 `min_stock`)也会被下次对账/刷新重开 —— 这是有意的保底设计(真实补货使库存高于 `min_stock`,故已真正恢复的定义永不为低,其行也不会被重开)。
- **TickTick / 外部待办同步:** 仅预留接缝(§12);真正的 OAuth2 + 双向实现排入集成里程碑家族(roadmap §6/§7)。
