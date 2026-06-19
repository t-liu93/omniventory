# M4 — 统一提醒与通知引擎（①②③ 主动告警）

> 🌐 **语言:** [English](./M4.md) · 中文(当前)

> **里程碑设计文档 — 自包含。** 配合 `docs/plan/roadmap.md`（地图，尤其 §5 M4 + 红线 **§2.6 唯一的统一提醒引擎** —— 多来源、可配置的"提前 N 天" *按条目、按用户*、事件触发 + 每日定时兜底、默认开启、通知责任人、可插拔通道 —— 以及 §2.7 保修作为提醒来源、§2.9 Decimal/Date、§2.10 单一 context 层、§2.11 逻辑在应用层不在 SQL）一起读；"我们为什么存在"见 `docs/inspiration/investigation.md`（三个参考项目的提醒失败 —— 硬编码 / 默认关 / 仅订阅者 / 仅拉取 —— 就是我们要跨过的标杆）。本文是 *M4 做什么、怎么验收* 的唯一真相源；不要从 roadmap 反推范围。进度只记录在 roadmap §4 表里。
>
> 内建约定：原子步骤（§9）、盲审检查点（§10）、🟢 部署自测点（§11），让手动与编排两种执行方式都能挂靠本文。
>
> **体量提示（先读）。** M4 是迄今最大的里程碑。作者拍板了**最大范围**：lead time 可配置到 **全局 + 每条目 + 每用户**；通道 = **in-app + 邮件/SMTP + HTTP + 完整 MQTT 桥接**（MQTT 桥接 —— 提醒发布 + 状态主题 + Home Assistant 自动发现 + 入站命令 —— 从 M9 提前到 M4）。因此拆成**四个阶段**（A 配置、B 引擎 + in-app、C 外部通道、D 前端）共 **12 个原子步骤**，每步都可独立部署：Phase B 之后即可演示（in-app 提醒已可用），Phase C/D 的通道未配置时会优雅降级。

---

## 1. 目标与非目标

**目标（roadmap 的 M4 承诺 —— 产品差异化点）：** **一个**提醒引擎、多来源、跨 ①②③ 主动"提前 N 天"告警。具体：

- **三个触发来源统一**：`best_before_date`（保质期 ①）、`warranty_expires`（耐用品保修 ②）、**低库存**（消耗品 ③）。（维护到期在 M7 加入；引擎按来源可插拔，到时直接插入。）
- **三个层级的可配置 lead time** —— **每来源全局默认**、**每条目覆盖**（在 definition 上）、**每用户覆盖**（在账户上）—— 配确定性的解析链（§4.3）。
- **两条触发路径（双保险，roadmap §2.6）**：**每日定时扫描**（APScheduler，在可配置的家庭本地时间）幂等地评估每个来源；**事件触发**在某次出入库使某 definition 跌破阈值的瞬间触发低库存（in-app 即时，不必等下一次扫描）。
- **持久化的通知模型**：一个 in-app **收件箱**（主通道），带**幂等去重**让每日扫描永不重复创建同一条提醒，以及**低库存 episode（一段持续）**模型，让持续短缺按可配置节奏（立即，然后例如第 1/3/7 天）复提醒，而不是每天刷屏或彻底沉默。
- **可插拔通道**投递每条新通知：**in-app**（始终开）、**邮件/SMTP**（每日摘要）、**HTTP**（出站 webhook **和**一个供 Home Assistant RESTful sensor 拉取的入站状态端点）、**完整 MQTT 桥接**（提醒发布 + 库存/到期/低库存状态主题 + Home Assistant 自动发现 + 入站命令主题）。
- **一张新的 KV 设置表**承载以上全部配置，并通过新的前端 **Configuration 页面**暴露（未来通用设置面的种子）。

**完成标准（🟢，§11 展开）：** 设全局保质期 lead = 3 天、保修 lead = 30；把一个条目覆盖成更长 lead、一个用户覆盖成更短；登记一个临期生鲜、一个临期保修、把一个消耗品压到低库存；**运行扫描**（手动 + 每日定时），看到 bell/收件箱里出现**一组合并的 in-app 通知**、一封**邮件摘要**覆盖三者、**MQTT 状态主题 + 提醒事件**发布（且一个被 Home-Assistant 自动发现的传感器反映计数）、**HTTP webhook** 触发且 **HA 状态端点**返回实时计数；让低库存持续，看到**复提醒**按节奏触发；补货后看到 **episode 关闭**（不再复提醒）。CI 保持绿，包括无漂移契约门禁。

**非目标（明确不在 M4 —— 推迟或属后续里程碑）：**
- **除提醒外不新增领域数据。** M4 *读取*已有的 `best_before_date` / `warranty_expires` / 低库存信号（M2/M3）；不改生鲜/耐用/消耗品建模。唯一的领域表变更是两个 **lead-time 覆盖**列（§3.4/§3.5）。
- **无维护到期来源。** 该来源（耐用品周期维护）属 **M7**；引擎的来源接口已为它预留、无需返工，但由 M7 实现。
- **无多用户投递路由 / 角色。** 提醒扇出到**所有 active 用户**作为收件人集合（M4 实际只有单管理员）。**责任人指派**与**角色可见性**属 **M6**；`notifications.user_id` 收件人列就是向前兼容的接缝。
- **除提醒 + 通道外无通用设置面。** Configuration 页面只发**提醒 + 通道**两节。家庭名/币种/时区编辑等后续再做；KV 表 + 页面按可扩展设计。
- **无 LLM / 第三方公共 REST API / 通用事件 webhook。** 这些留 **M9**。M4 的 HTTP 仅限 *提醒 webhook 出* + *集成状态读 入*；M4 的 MQTT 是 HA 桥接。（M9 保留更广的公共 API + 通用 webhook + LLM 功能。）
- **无回溯重放。** 打开某通道**不会**补投历史通知；改 lead time 只影响**未来**扫描评估，不影响已创建的通知。

---

## 2. 锁定决策（M4 规划中敲定；理由见 roadmap §2/§3 + investigation 第 3 章）

| 领域 | 决策 |
|---|---|
| **一个引擎、来源可插拔** | 单一 `ReminderEngine.run_scan()` 在一遍内评估所有来源，且**幂等**（一天内任意次重跑安全）。每个来源是一个小评估器（`best_before`、`warranty`、`low_stock`），藏在统一接口后，让 **维护到期（M7）** 不动其它来源即可插入。 |
| **收件人 = 所有 active 用户（M4）** | 每条通知带一个 **`user_id`**（收件人）。M4 引擎扇出到**所有 `is_active` 用户**（通常就一个管理员）。**责任人收窄 + 角色 = M6**；该列是接缝，所以 M6 是过滤变更，不是 schema 变更。 |
| **lead time：全局 → 每条目 → 每用户，最具体优先** | 日期来源 lead 天数按 **每条目覆盖（definition）→ 每用户覆盖（账户）→ 全局默认（settings）** 解析 —— 第一个非空胜出（§4.3）。每条目表达*物品*本性（护照保修 = 90 天、牛奶 = 2）；每用户是账户的统一兜底；全局是底线。低库存**无 lead**（它是状态不是日期）；其节奏是复提醒计划表。 |
| **日期来源对 (收件人, 批次, 目标日期) 只触发一次** | best_before / warranty 在 `today_local ≥ target_date − lead` 时触发**一条**通知。去重键 = `"{source}:u{uid}:i{lot}:{target_date}"`。编辑批次日期 ⇒ 新键 ⇒ 全新提醒（正确）。临近不每天复触发（刻意从简；"也在当天/逾期时再触发"的细化见 §12）。 |
| **低库存 = 立即 + 复提醒 *episode*** | definition 转低**开启一段 episode**：opener 通知（offset 0）立即触发。当它**保持低**且**未解决**时，对每个配置的 offset（默认 `[1, 3, 7]`），当 `从 episode 起算的天数 ≥ offset` 时各触发一次。补货后**关闭 episode**（置 `resolved_at`）。之后再转低开启**新** episode（新锚点日期 ⇒ 新去重命名空间）。opener 通知行*就是* episode 记录（无单独表）—— 见 §3.3。 |
| **两条触发路径（双保险）** | **每日 APScheduler 扫描**在可配置的**家庭本地**时间评估**所有**来源。**事件触发**在某次 `consume`/`discard`/`adjust` 使某 definition 跌破阈值的瞬间运行**低库存**评估器（即时 in-app + 即时通道；邮件**摘要**仍等每日扫描）。日期来源不需要事件钩子（日期跨入窗口只在日期翻天时发生，由每日扫描负责）。 |
| **"今天" / 扫描时间遵循 `household.timezone`** | 扫描在家庭本地时间触发，所有日期比较（`today_local`）用 `household.timezone`（落实 M3 §12 的推迟项）。一个家庭，一个时钟。 |
| **新的 KV 设置表** | 新建**面向用户的 `settings`** 表（`key` 主键，`value` TEXT/JSON）。与 `app_config`（服务端管理的密钥 —— 用户永不可编辑）和 `household.settings`（JSON blob，保持不动）**区分**。提醒 + 通道配置全在此，点号命名（`reminders.*`、`channels.*`）。一个有类型的 `SettingsService` 读写，**默认值写在代码里**（未设置的键返回其默认；表里只存覆盖）。 |
| **通道密钥在线上只写不读** | SMTP/MQTT 密码与集成 token 通过设置 API **写入**但在 GET 中**永不回显**（改返回 `*_is_set: bool` 标志）。用户填的密钥存在 `settings`；只有自动生成的服务端 `secret_key` 留在 `app_config`。 |
| **in-app 保持 code+params；邮件/MQTT/HTTP 携带服务端渲染的文本** | 按 wire/display 拆分（M1.5），**in-app** 通道存 **`message_code` + `params`**，由**前端**本地化（`notifications` 命名空间）。邮件/MQTT/HTTP 负载**源于服务端、不经前端**，所以后端从一个**小的双语服务端目录**（ZH+EN，按同一批码键）按**收件人的 `preferred_language`**（为空则家庭默认）渲染人类可读文本。这是对"后端不输出展示文本"的**有界、刻意例外**，理由是这些产物离开系统时从不经过 SPA。外部负载是 `{code, params, message}`，机器消费者（HA）用结构化部分，人读 `message`。 |
| **通道在 dispatcher 后可插拔** | `NotificationDispatcher` 遍历**已启用**的通道适配器（`InAppChannel` 隐式 = 行本身；`EmailChannel`、`HttpChannel`、`MqttChannel`）。逐条**即时**通道（HTTP、MQTT）对每条新通知触发（扫描 + 事件路径）；**邮件**仅**摘要**（在每日扫描末尾打包）。每次外部尝试记入 `notification_deliveries` 且**幂等**（通道跳过已投递的通知）。网络 I/O 在通知行提交**之后**发生 —— 绝不在 DB 事务内；失败为尽力而为（记录、留痕），绝不让扫描或出入库崩溃。 |
| **MQTT = 完整 Home Assistant 桥接（从 M9 提前）** | 一个由 FastAPI lifespan 管理的长连 `paho-mqtt`（启动时若启用则连接；关闭时干净断开；自动重连）。它**发布**提醒事件 + **状态主题**（`low_stock_count`、`expiring_count`、`expired_count`…），发出 **Home Assistant MQTT 自动发现**配置让 HA 自动建传感器，并**订阅**命令主题接受一组**有界**操作（`consume`、`intake`、`adjust`）。命令通过既有 service 以配置的系统执行者身份执行；未知/畸形命令丢弃 + 记录。 |
| **HTTP = 出站 webhook + 入站 HA 状态端点** | (a) 一个**出站** `HttpChannel` 把每条新通知（`{code, params, message}`）POST 到配置的 URL，带可选 auth 头。(b) 一个**入站** `GET /api/integrations/state` 以 JSON 返回实时计数，供 Home Assistant 的 **RESTful sensor** 轮询；它由一个**静态集成 token**（头/查询）鉴权，*非*会话 cookie（HA 持不了 cookie）。出站 URL 的基础 **SSRF 备注**已记录；强化守卫属 **M6**（roadmap §5 M6）。 |
| **新依赖** | 后端新增 **`apscheduler`**（调度）、**`paho-mqtt`**（MQTT），并把 **`httpx`** 提为**运行时**依赖（出站 webhook + 集成测试已用它）。邮件用**标准库** `smtplib`/`email`。这些是栈新增 ⇒ `AGENTS.md` "Tech stack & commands" 后端行加上它们（基础性改动，随 Step 1 完成）。 |
| **错误码（最小新增）** | 新增：`notification.not_found`（对不存在/非本人的通知标记已读，404）和 `integration.invalid_token`（状态端点 token 错/缺，401）。设置校验走 Pydantic ⇒ 既有 `validation.invalid_input`。MQTT 命令失败映射到既有 `stock.*` 码（记录，非 HTTP）。所以 M4 新增**两个** `ErrorCode`。 |

> 这些扩展 M0–M3 基础。两个新错误码、新表/列、每个新 schema、新端点都流经既有无漂移契约门禁（§6）与 M1.5 统一错误信封。新后端依赖 + 调度/MQTT 运行时接线是唯一的"基础性"触碰（AGENTS.md 后端行 + 一个 `scheduler`/`mqtt` 设置开关）。

---

## 3. 数据模型

三张新表 + 两个增量列。全部沿用 M0 约定：在共享 `Base` 上用 SQLAlchemy 2.0 有类型 `Mapped[...]`，规则在 service 层，DB 访问**只**经 repository。两个列均为可空纯增（无需重建表）。

### 3.1 `settings` —— 新表（迁移 `0015`）

面向用户的键值配置。与 `app_config`（服务端密钥）和 `household.settings`（JSON blob）**区分**。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `key` | String(128) | 否 | 主键。点号命名（`reminders.best_before.lead_days`、`channels.mqtt.host`…）。 |
| `value` | Text | 否 | 以文本存；结构化值（如复提醒列表）JSON 编码。由有类型的 `SettingsService` (反)序列化。 |
| `updated_at` | DateTime(tz) | 否 | `server_default=now()`，upsert 时刷新。 |

只存**被覆盖**的键；未设置的键经 `SettingsService` 返回其**代码定义的默认**。用 `Session.merge` upsert（`AppConfigRepository` 已用的可移植模式）。

### 3.2 `notifications` —— 新表（迁移 `0018`）

in-app 收件箱 + 去重账本 + 低库存 episode 记录（opener 行*就是* episode）。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | 主键。 |
| `user_id` | FK→users.id | 否 | **收件人**（§2 接缝）。`ondelete=CASCADE`。 |
| `source` | String(32) | 否 | `best_before` / `warranty` / `low_stock`。 |
| `subject_type` | String(32) | 否 | `instance`（日期来源）/ `definition`（低库存）。 |
| `subject_id` | Integer | 否 | 被引批次或 definition id（无硬 FK —— 删除后作为历史记录保留；存在性由引擎关心）。 |
| `dedup_key` | String(255) | 否 | 幂等键（§4.4/§4.5）。**与 `user_id` 唯一**（`uq_notifications_user_dedup`）。 |
| `message_code` | String(64) | 否 | i18n 码（`reminder.best_before`、`reminder.warranty`、`reminder.low_stock`、`reminder.low_stock_repeat`）。 |
| `params` | Text | 是 | JSON 渲染参数（name、date、days_remaining、qty、threshold、offset、location…）。 |
| `episode_started_on` | Date | 是 | 仅低库存：episode 锚点日期。日期来源为 NULL。 |
| `offset_days` | Integer | 是 | 仅低库存：本行是哪个计划 offset（`0` = opener）。日期来源为 NULL。 |
| `resolved_at` | DateTime(tz) | 是 | 仅低库存：在 definition 恢复时置于 **opener**（关闭 episode）。NULL = 开启 / 不适用。 |
| `read_at` | DateTime(tz) | 是 | in-app 已读状态。NULL = 未读。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

索引：`uq_notifications_user_dedup`（唯一幂等）；一个非唯一 `(user_id, read_at)` 让未读计数/收件箱查询便宜。

### 3.3 低库存 episode 模型（无额外表）

一段 **episode** = "对该收件人，这个 definition 自日期 D 起持续低"。它由其 **opener 通知**表示（`source=low_stock`、`offset_days=0`、`episode_started_on=D`、开启期间 `resolved_at` NULL）。复提醒是兄弟行（`offset_days ∈ repeat_schedule`），共享同一 `episode_started_on`。引擎：
- 当 definition 低且 `(user, def)` **无开启的 opener** 时**开启** episode（插入 opener）；
- 通过插入 offset `≤ today_local − episode_started_on` 且尚不存在的 offset 行来**复提醒**；
- 当 definition 不再低时**关闭**（给开启的 opener，及为整洁起见其开启的复提醒，盖 `resolved_at`）。

这让一张表同时做收件箱 + 去重 + episode 状态；`(user_id, dedup_key)` 唯一使每次插入幂等。（曾考虑专门的 `episodes` 表，因对单一状态来源属过度设计而否决 —— §12 记权衡。）

### 3.4 `item_definitions` —— 新增（迁移 `0016`）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `reminder_lead_days` | Integer | 是 | **每条目 lead 覆盖**（`≥ 0`，Pydantic 校验）。作用于该 definition 批次携带的任一日期来源（生鲜的 best_before、耐用的 warranty）。NULL = 继承（§4.3）。 |

> 单一覆盖字段（非每来源一个）是刻意的：一个 definition 的批次实际上是同一类。每来源每条目覆盖是记录的细化（§12）。

### 3.5 `users` —— 新增（迁移 `0017`）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `reminder_best_before_lead_days` | Integer | 是 | **每用户** best-before lead 覆盖（`≥ 0`）。NULL = 继承全局。 |
| `reminder_warranty_lead_days` | Integer | 是 | **每用户** warranty lead 覆盖（`≥ 0`）。NULL = 继承全局。 |

> 沿用既有可空 `preferred_language` 的生命周期。每用户**通道**偏好与每用户**低库存**节奏不在 M4（§12 记）。

### 3.6 `notification_deliveries` —— 新表（迁移 `0019`）

roadmap §3 的"投递日志"。由外部通道写入（in-app 隐式）。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | 主键。 |
| `notification_id` | FK→notifications.id | 否 | `ondelete=CASCADE`。 |
| `channel` | String(32) | 否 | `email` / `http` / `mqtt`。 |
| `status` | String(16) | 否 | `sent` / `failed`。 |
| `detail` | String(1024) | 是 | 失败时的错误文本（截断）。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

幂等：通道跳过对该通道已有 `sent` 行的通知（`failed` 行可在下一遍重试）。索引 `(notification_id, channel)`。

### 3.7 迁移清单

| Rev | Step | 做什么 |
|---|---|---|
| `0015` | 1 | 建 `settings`。 |
| `0016` | 2 | `item_definitions`：加 `reminder_lead_days`。 |
| `0017` | 2 | `users`：加 `reminder_best_before_lead_days`、`reminder_warranty_lead_days`。 |
| `0018` | 3 | 建 `notifications`。 |
| `0019` | 7 | 建 `notification_deliveries`。 |

均可用纯 `op.create_table` / `op.drop_table` / `op.drop_column` 回退。无数据回填（新表空；新列默认 NULL = "继承 / 无覆盖"）。

---

## 4. 后端设计

### 4.1 分层（扩展 M2/M3）

- **Repository**（`app/repositories/`）：`SettingsRepository`（get/set/get_all —— 像 `AppConfigRepository` 那样 `merge` upsert）；`NotificationRepository`（按 dedup 不存在则创建、按用户列出、未读计数、标记已读/全部、查找开启的低库存 opener、列出某通道未投递、标记 resolved）；`NotificationDeliveryRepository`（记录、存在已 sent）。`ItemDefinitionRepository` / `UserRepository` 串接新覆盖列。repository 外**无裸查询**。
- **Service**（`app/services/`）：
  - `SettingsService` —— 有类型访问器 + 代码定义默认；校验；**只写密钥**处理。
  - `ReminderEngine` —— 编排者：`run_scan()`（所有来源、所有收件人）、`evaluate_low_stock(definition_id)`（事件触发的收窄路径）、lead 解析（§4.3）、去重、episode 生命周期，然后分发。
  - `NotificationService` —— 供 API 的 in-app 收件箱读写（列出、未读计数、标记已读）。
  - `NotificationDispatcher` + 通道适配器（`app/notifications/channels/`）：`EmailChannel`、`HttpChannel`、`MqttChannel`；服务端消息目录（`app/notifications/messages.py`，ZH+EN）。
  - `IntegrationStateService` —— 为入站 HA 端点计算计数 JSON（复用 `LowStockService` / `ExpiryService`）。
- **引擎复用既有信号**：低库存经 `LowStockService.compute()`；到期候选经 best_before 批次查询；保修经 warranty 批次查询。不重写这些规则。
- **调度器**（`app/scheduler.py`）：一个薄封装，在 lifespan 启动 APScheduler `BackgroundScheduler`，从 `reminders.scan_time` 注册每日任务。由 `scheduler_enabled` 设置开关（测试中关）。

### 4.2 收件人与"今天"

`run_scan()`：`today_local = now(household.timezone).date()`。`recipients = UserRepository.list_active()`。引擎对 收件人 × 来源 循环。（单管理员 ⇒ 一遍；结构可扩展，M6 把 `recipients` 收窄到责任人。）

### 4.3 lead time 解析（易错链 —— 必测）

对日期来源 `s ∈ {best_before, warranty}`、definition `d`、收件人 `u`：

```
resolve_lead(s, d, u):
    if d.reminder_lead_days is not None:        # 1. 每条目覆盖（两个日期来源）
        return d.reminder_lead_days
    per_user = u.reminder_best_before_lead_days if s == best_before
               else u.reminder_warranty_lead_days
    if per_user is not None:                    # 2. 每用户覆盖（按来源）
        return per_user
    return settings.lead_days(s)                # 3. 全局默认（按来源）
```

第一个非空胜出；**每条目 > 每用户 > 全局**。所有值 `≥ 0`（Pydantic）。lead 为 `0` ⇒ 当天触发。

### 4.4 日期来源（best_before、warranty）

对每个收件人 `u`、每个携带该来源日期的**存活**批次（`best_before_date` / `warranty_expires` 非 NULL；存活 = `quantity IS NULL OR quantity > 0`）：

```
lead   = resolve_lead(source, lot.definition, u)
window = lot.target_date - timedelta(days=lead)
if today_local >= window:
    dedup = f"{source}:u{u.id}:i{lot.id}:{lot.target_date.isoformat()}"
    NotificationRepository.create_if_absent(
        user_id=u.id, source=source, subject_type="instance", subject_id=lot.id,
        dedup_key=dedup, message_code=f"reminder.{source}",
        params={ "name": lot.definition.name, "date": lot.target_date,
                 "days_remaining": (lot.target_date - today_local).days,
                 "location_id": lot.location_id })
```

对 `(u, lot, date)` 触发一次；重跑扫描是空操作；编辑日期产生新键。

### 4.5 低库存来源（立即 + 复提醒 episode）

复提醒 offset：`settings.low_stock_repeat_days()`（默认 `[1, 3, 7]`，排序去重，每个 `≥ 1`）。令 `low_now = { LowStockItem.definition_id }` 来自 `LowStockService.compute()`。

```
for u in recipients:
    for def_id in low_now:
        opener = NotificationRepository.open_low_stock_opener(u.id, def_id)   # offset 0, resolved_at NULL
        if opener is None:
            create opener: offset_days=0, episode_started_on=today_local,
                dedup=f"low_stock:u{u.id}:d{def_id}:{today_local.isoformat()}:o0",
                message_code="reminder.low_stock", params={name, current, threshold, mode}
        else:
            elapsed = (today_local - opener.episode_started_on).days
            for o in repeat_offsets where o <= elapsed:
                dedup=f"low_stock:u{u.id}:d{def_id}:{opener.episode_started_on.isoformat()}:o{o}"
                create_if_absent: offset_days=o, episode_started_on=opener.episode_started_on,
                    message_code="reminder.low_stock_repeat", params={name, current, threshold, mode, offset:o}
    # 关闭已恢复的 episode
    for opener in NotificationRepository.open_low_stock_openers(u.id) where opener.subject_id not in low_now:
        mark_resolved(opener)   # 给 opener（及其开启的复提醒）盖 resolved_at
```

`create_if_absent` 依赖 `(user_id, dedup_key)` 唯一。`elapsed ≥ o`（非 `==`）让漏掉的某天仍能补上；每个 offset 触发一次（去重）。**事件触发**调用 `evaluate_low_stock(def_id)` —— 同一逻辑收窄到单个 definition 跨收件人 —— 紧接出入库之后、在同一事务内（与出入库原子）；每日扫描兜底。

### 4.6 分发与通道

扫描/事件提交新通知行后，`NotificationDispatcher.dispatch(new_notifications, *, include_email_digest)`：
- **in-app**：隐式（行已存在）。
- **HTTP / MQTT（即时）**：对每条该通道尚未 `sent` 的新通知，渲染 `{code, params, message}`（message 经服务端目录按收件人语言）并投递；记一条 `notification_deliveries`。
- **邮件（摘要）**：仅当 `include_email_digest`（每日扫描）：把当天的新通知**按收件人**分组，按该收件人语言渲染一封摘要邮件经 SMTP 发到该用户邮箱；对每条纳入的通知记一条投递行。

事件路径 → `dispatch(new, include_email_digest=False)`；扫描 → `dispatch(new, include_email_digest=True)`。所有通道 I/O 在**提交之后**、尽力而为、包裹起来，使通道错误被记录 + 留痕但绝不外传。

通道适配器从 `SettingsService` 读其配置；停用/未配置的通道是**空操作**（跳过）。`EmailChannel` 用标准库 `smtplib`（+`use_tls`）；`HttpChannel` 用 `httpx`（短超时）；`MqttChannel` 经共享 `paho-mqtt` 客户端发布（§4.8）。

### 4.7 调度器（APScheduler）

`app/scheduler.py` 在 `_lifespan` 中当 `settings.scheduler_enabled`（默认 True；**`environment == "test"` 与单测中为 False**）时启动 `BackgroundScheduler`。它从 `reminders.scan_time` 注册每日 `CronTrigger(hour, minute, timezone=household.timezone)`；任务开自己的 DB 会话、跑 `ReminderEngine.run_scan()`、提交、再分发。配置变更时重注册是记录的细化（§12）；M4 里 `scan_time` 变更在下次重启生效，而按需端点覆盖即时需求。**单 worker 假设**（Docker 镜像跑一个 uvicorn worker）；多 worker 锁见记（§12）。**引擎直接单测**（同步调 `run_scan()`）—— APScheduler 是薄接线，不在测试热路径里。

`POST /api/reminders/run`（需鉴权）按需触发 `run_scan()` 并返回小摘要（每来源创建计数）—— 🟢 走查用它代替等定时器。

### 4.8 MQTT 桥接（完整）

一个由 lifespan 持有的 `MqttBridge`（paho-mqtt），当 `channels.mqtt.enabled` 时启动：
- **连接** `host:port`（可选 TLS / 用户名 / 密码）；后台线程 `loop_start()`；paho 自动重连。主题前缀来自 `channels.mqtt.topic_prefix`（默认 `omniventory`）。
- **提醒发布**：`MqttChannel` 把每条新通知发到 `{prefix}/notifications/{source}`（`{code, params, message}`，retained=false）。
- **状态主题**：每次扫描后（及低库存事件后）把计数发到 `{prefix}/state/low_stock_count`、`/expiring_count`、`/expired_count`（retained=true，使 HA 重连时看到上次值）。
- **Home Assistant 自动发现**：连接时（及配置变更时）把 HA MQTT 发现配置发到 `homeassistant/sensor/omniventory_<metric>/config`，使 HA 自动建绑定状态主题的传感器。由 `channels.mqtt.discovery_enabled` 开关。
- **入站命令**：订阅 `{prefix}/command/#`；接受一组**有界** —— `consume {definition_id, quantity}`、`intake {instance_id, quantity}`、`adjust {instance_id, counted_quantity}` —— 在新会话中以配置的**系统执行者**（保留 user id / 空执行者）经 `StockMovementService` 执行，把结果发到 `{prefix}/command_result`。未知主题/操作或畸形负载 ⇒ 丢弃 + 记录。由 `channels.mqtt.commands_enabled` 开关（默认 **false** —— 因其改库存，需显式开）。命令鉴权/信任边界备注见 §12（broker 由运维信任；强化属 M6）。

### 4.9 HTTP 集成

- **出站** `HttpChannel`（即时）：把 `{code, params, message}` POST 到 `channels.http.webhook_url`，带可选 `channels.http.auth_header`；`httpx` 短超时；记投递。停用/未设 ⇒ 空操作。**SSRF 备注**（§12）：M4 做基础公网 URL 合理性检查；完整守卫属 M6。
- **入站** `GET /api/integrations/state` → `{ low_stock_count, expiring_count, expired_count, generated_at }` 供 HA 的 RESTful sensor。**鉴权 = 静态集成 token**（`X-Omniventory-Token` 头或 `?token=`），与 `settings` 的 `channels.http.integration_token` 比对（启用时首读自动生成；可重生成）。缺/错 ⇒ `integration.invalid_token`（401）。它**不**在会话 cookie 依赖之后。

### 4.10 API 面（增量；全在 `settings.api_prefix` 下，默认 `/api`）

| 方法 + 路径 | 鉴权 | 用途 |
|---|---|---|
| `GET /settings` | session | 读提醒 + 通道配置组（密钥掩码 → `*_is_set` 标志）。 |
| `PATCH /settings` | session | 更新配置（校验；只写密钥字段）。 |
| `GET /notifications?unread_only=&limit=` | session | 当前用户收件箱（最新在前）。 |
| `GET /notifications/unread-count` | session | 角标计数。 |
| `POST /notifications/{id}/read` | session | 标记一条已读（不存在/非本人 ⇒ 404 `notification.not_found`）。 |
| `POST /notifications/read-all` | session | 标记当前用户全部已读。 |
| `POST /reminders/run` | session | 立即触发 `run_scan()`；返回每来源创建计数摘要。 |
| `GET /integrations/state` | **token** | HA RESTful-sensor 计数（401 `integration.invalid_token`）。 |
| `POST /definitions` · `PATCH /definitions/{id}` | session | 现也接受 **`reminder_lead_days`**（`int ≥ 0`，可空）。 |
| `GET/PATCH /users/me`（或既有 me/profile 路由） | session | 现也接受/返回 **`reminder_best_before_lead_days`**、**`reminder_warranty_lead_days`**。 |

> `GET /settings` 把密钥只返回布尔；`PATCH` 接受密钥以设置，接受显式空/`null` 以清除。具体 `me`/profile 路由就是 M1.5 为 `preferred_language` 加的那个 —— 扩展它，别加并行路由。

### 4.11 Schema（`app/schemas/`）

- `SettingsResponse` / `SettingsUpdate` —— 有类型的配置组（reminders：`best_before_lead_days`、`warranty_lead_days`、`low_stock_repeat_days: list[int]`、`scan_time: "HH:MM"`；channels：email/http/mqtt 子对象带 `enabled` + 字段，密钥在 `Update` 中只写、在 `Response` 中为 `*_is_set`）。校验：lead `ge=0`，复提醒列表项 `ge=1`，`scan_time` 正则，端口 `ge=1 le=65535`。
- `NotificationResponse` —— `id, source, subject_type, subject_id, message_code, params, offset_days, created_at, read_at`。（无服务端文本 —— SPA 从 `message_code` + `params` 本地化。）
- `UnreadCountResponse` —— `{ count: int }`。
- `ReminderRunSummary` —— `{ best_before: int, warranty: int, low_stock: int }`（创建计数）。
- `IntegrationStateResponse` —— `{ low_stock_count, expiring_count, expired_count, generated_at }`。
- Definition/Instance/User schema 加上覆盖字段。

---

## 5. 质量门禁与易错逻辑（Definition of Done）

继承 M0–M3 §5（`make check` 全绿；构建；`make codegen` 无漂移）。M4 中**必须有单测的逻辑**（roadmap DoD —— *到期/lead-time 日期计算、阈值/低库存触发、幂等*）：

**后端**
- **lead 解析链**（§4.3）：每条目胜过每用户胜过全局；NULL 逐级穿透；`0` lead 当天触发；两个来源各取正确的每用户字段。
- **日期来源触发与去重**（§4.4）：恰在 `today_local ≥ date − lead` 时触发、之前不触发；**边界**（`== window` 触发，`window − 1 天` 不触发）；第二次扫描不创建任何东西（幂等）；编辑日期产生新通知；已过期（过去日期）批次触发；耗尽的 `exact` 批次（`quantity 0`）不触发；带日期的 `level`/`none` 批次（qty NULL）触发；用 `household.timezone` 算 `today`。
- **低库存 episode**（§4.5 —— 必测）：转低时 opener 触发一次；**复提醒**在每个 offset（`elapsed ≥ offset`）各触发一次；漏掉的某天能**补上**（仍触发，不跳过）；**恢复关闭** episode（置 `resolved_at`，不再复提醒）；**再次**转低开启**新** episode（新锚点，全新去重）；事件触发与每日扫描产出**相同**行（不重复插入）。
- **设置**：未设置时返回默认；upsert 覆盖；校验（lead `< 0`、复提醒项 `< 1`、坏 `scan_time`、坏端口）→ `validation.invalid_input`；密钥永不回显（只 `*_is_set`）；设置/清除一个密钥。
- **dispatcher 幂等**：通道跳过已 `sent` 的通知；通道错误被记录（`failed`）且绝不抛出；邮件**摘要**按收件人分组；停用通道 = 空操作。
- **in-app API**：列出（最新在前、未读过滤）、未读计数、标记一条（非本人 id ⇒ `notification.not_found` 404）、标记全部；用户只见自己的行。
- **集成 token**：有效 token ⇒ 计数；缺/错 ⇒ `integration.invalid_token`（401）；计数与 `LowStockService`/`ExpiryService` 一致。
- **MQTT**（paho 客户端打桩 —— 测试中无真实 broker）：发布为提醒/状态/发现构造正确的主题+负载；命令负载映射到正确的 service 调用；畸形/未知命令被丢弃（无异常、无改动）；停用时桥接空操作。
- **迁移往返**：`0015`–`0019` 在 `0014` 的 DB 上干净升级、干净降级；既有行不受影响（新列 NULL）。

**前端**（vitest + Testing Library，打桩有类型客户端 —— M0 风格）：**bell** 显示未读计数，下拉从 `message_code`+`params` 本地化列出通知；**标记已读**清角标；**/notifications 页**列出/过滤；**Configuration 页**加载当前设置、编辑 lead/复提醒/scan-time/通道字段、掩码密钥（显示"已设/未设"）并 PATCH；item 表单上的**每条目 lead**字段；profile/设置上的**每用户 lead**字段；日期/数字经 M1.5 `formatDate`/`formatQuantity`；所有文案 **en+zh**。

---

## 6. 契约优先 codegen 与无漂移门禁

机制不变（M0/M1/M2/M3 §6）。触碰 API 的步骤**重跑 `make codegen`** 并提交 `openapi.json` + `frontend/src/api/schema.d.ts`；CI **contract** 任务在漂移时失败。M4 用设置组、`NotificationResponse`、`UnreadCountResponse`、`ReminderRunSummary`、`IntegrationStateResponse`、覆盖字段与新路径扩展 schema。**不**触 schema 的步骤（如 MQTT 桥接内部、调度器接线）无需 codegen —— 其步骤说明会写明。

---

## 7. 前端设计

### 7.1 通知 bell + 收件箱（`shell/AppShell.tsx` 头部 + `components/NotificationBell.tsx`）
- 头部一个 **bell** `ActionIcon`，带未读计数 `Badge`（按间隔轮询 `GET /notifications/unread-count`、并在标记已读后刷新）。点击 → 一个 `Menu`/`Popover` 下拉列出近期通知，每条从 `message_code` + `params` 经 `notifications` 命名空间**本地化**（如 `t('reminder.best_before', { name, days_remaining })`），带相对时间与已读/未读提示；"全部已读" + 通往整页的链接。

### 7.2 /notifications 页（`pages/Notifications.tsx`，新）+ 路由
- 整列（最新在前）、一个**仅未读**过滤、逐行标记已读、行链向 subject（`/instances/:id` 或 `/items/:id`）。在 `App.tsx` 注册 `<Route path="/notifications">`；在 `NavContent` 加一个导航项（bell/收件箱项）。

### 7.3 Configuration 页（`pages/Configuration.tsx`，新）+ 路由
- 一个新的 **Configuration** 页（导航项，如 `Settings` 图标）—— 未来设置面的种子，M4 限定为：
  - **提醒**：全局 `best_before_lead_days`、`warranty_lead_days`（NumberInput，min 0）；`low_stock_repeat_days`（小列表编辑器 / 逗号输入 → `int[]`，每个 ≥ 1）；`scan_time`（时间输入 "HH:MM"）。
  - **你的提醒（每用户）**：当前账户的 `reminder_best_before_lead_days` / `reminder_warranty_lead_days` 覆盖（空 = 继承）。
  - **通道**：Email/SMTP（`enabled`、host、port、username、**密码只写**显示"已设/未设"、`use_tls`、from、收件人备注）；HTTP（`enabled`、webhook URL、auth 头；**集成 token** 带复制/重生成控件 + 给 HA 的状态端点 URL 提示）；MQTT（`enabled`、host、port、username、**密码只写**、topic 前缀、TLS、`discovery_enabled`、`commands_enabled`）。
  - 一个 **"立即运行扫描"** 按钮（`POST /reminders/run`）显示创建计数摘要 —— UI 内演示引擎的方式。
- 读 `GET /settings`，写 `PATCH /settings`；密钥只写（永不预填；显式清除）。

### 7.4 每条目 lead 覆盖（`pages/Items.tsx` / `DefinitionFormModal`）
- definition 表单加一个 **`reminder_lead_days`** NumberInput（可选，min 0，"天；为此物品覆盖全局 lead"）。设置后在 definition 详情显示。

### 7.5 i18n
- 新 **`notifications`** 命名空间（bell/收件箱文案 + 四个带插值的 `reminder.*` 消息模板，**en+zh**）和 **`configuration`** 命名空间（页/节/字段标签），在 `src/i18n/index.ts` 注册。`items`（`reminder_lead_days`）与 `nav`（notifications、configuration）补充。`notification.not_found` 与 `integration.invalid_token` 的新 **`errors`** 键。测试钉 `en`（M1.5）。

### 7.6 测试
按 §5 "前端"：bell 计数 + 下拉本地化 + 标记已读；/notifications 列表/过滤；Configuration 加载/编辑/掩码/PATCH + 运行扫描；每条目与每用户 lead 字段。沿用 M0 "打桩有类型客户端"风格；无真实网络。

---

## 8. CI 与 Docker（相对 M3 的增量）

- **新后端依赖**（`apscheduler`、`paho-mqtt`、`httpx` 运行时）加入 `backend/pyproject.toml` + `uv.lock`；`AGENTS.md` 后端依赖行更新（Step 1）。
- **CI/测试中关掉调度器/MQTT**：`environment=test` 下 `scheduler_enabled=false` 且 MQTT 停用，使 `pytest` 不起线程/连接；引擎直接测。
- **Docker**：**migrate** 冒烟步骤现应用 `0001`–`0019`。单镜像不变；app 进程在进程内承载调度器 + MQTT 桥接（单 worker）。无新容器。新的可选 `.env`/设置（SMTP/MQTT/HTTP）全为**零配置默认关** —— 镜像在啥都没配时仍能启动。
- 其余（契约门禁、缓存、fail-closed `migrate` service）完全同 M0 §8 / M3 §8。

---

## 9. 步骤拆分（原子、有序）

每步独立可测、有测试支撑、落恰好一个提交（编排模式下每步一次 autosquash），若触碰 API 则重跑 `make codegen`，并继承全局 DoD（§5）+ **反夹带规则**：只实现*本步* —— 不做其它步、不夹带重构、不碰宿主真实环境（无真实 DB/容器/broker/SMTP）。

> **分阶段：** A（配置基座）→ B（引擎 + in-app，可演示核心）→ C（外部通道）→ D（前端）。Phase B 之后应用即可用；Phase C 各通道在未配置时各自可跳过/空操作。

### Phase A — 配置与覆盖

**Step 1 — 设置 KV 存储 + 提醒配置 + 依赖**
- **构建：** 迁移 `0015`（`settings`）；`Setting` 模型；`SettingsRepository`；`SettingsService`（有类型访问器 + 代码默认：`best_before_lead_days=3`、`warranty_lead_days=30`、`low_stock_repeat_days=[1,3,7]`、`scan_time="08:00"`，所有通道 `enabled=false`）；`SettingsResponse`/`SettingsUpdate`（密钥只写 / `*_is_set`）；`GET/PATCH /settings`；加后端依赖（`apscheduler`、`paho-mqtt`、`httpx` 运行时）+ 更新 `AGENTS.md` 后端行；`make codegen`。
- **测试：** 未设置时默认；upsert；校验（lead<0、复提醒<1、scan_time、端口）→ `validation.invalid_input`；密钥掩码 + 设置/清除；迁移 `0015` 升/降。
- **提交：** `feat(backend): KV settings store and reminder/channel configuration`

**Step 2 — 每条目 + 每用户 lead 覆盖**
- **构建：** 迁移 `0016`（`item_definitions.reminder_lead_days`）+ `0017`（`users.reminder_*_lead_days`）；串接模型、repo、schema（`DefinitionCreate/Update/Response`、`me`/profile schema）；`make codegen`。
- **测试：** 存/回显；`<0` 拒绝；NULL 默认；profile 读/更新两个用户字段；迁移升/降。
- **提交：** `feat(backend): per-item and per-user reminder lead overrides`

### Phase B — 引擎与 in-app（可演示核心）

**Step 3 — 通知表 + 引擎（日期来源）+ dispatcher 骨架 + in-app**
- **构建：** 迁移 `0018`（`notifications`）；`Notification` 模型 + `NotificationRepository`（按 dedup 不存在则创建、开启 opener 查找、列表、标记已读/全部、标记 resolved）；lead 解析（§4.3）；`ReminderEngine.run_scan()` 对每收件人评估 **best_before + warranty**（§4.4）带去重；`NotificationDispatcher` 仅 **in-app**（通道协议为 C 打桩）；`POST /reminders/run` 返回摘要；`make codegen`。
- **测试：** lead 链；日期来源触发/边界/幂等/编辑产新键/时区；过期 & 存活批次过滤；多收件人扇出。
- **不在范围：** 低库存（4）、调度器（5）、收件箱 API（6）、外部通道（7–10）。
- **提交：** `feat(backend): reminder engine with best-before and warranty sources`

**Step 4 — 低库存来源（立即 + 复提醒 episode）+ 事件钩子**
- **构建：** 扩展 `ReminderEngine` 加 `low_stock` 评估 + episode 开/复/关（§4.5）复用 `LowStockService`；`evaluate_low_stock(def_id)` 收窄路径；挂入 `StockMovementService` 的 consume/discard/adjust（同事务，尽力而为）。**无 schema 变更 → 无 codegen**（episode 列在 `0018` 已发）。
- **测试（必测）：** opener 一次；offset 复提醒带补偿；恢复关闭；再次转低新 episode；事件与扫描一致；不重复插入。
- **提交：** `feat(backend): low-stock reminders with repeat episodes and event trigger`

**Step 5 — APScheduler 每日扫描 + 时区**
- **构建：** `app/scheduler.py`（lifespan 中 `BackgroundScheduler`，由 `scheduler_enabled` 开关，从 `reminders.scan_time` 在 `household.timezone` 注册每日 `CronTrigger`，任务跑 `run_scan()` + 分发）；接入 `create_app` lifespan。**无 schema 变更 → 无 codegen。**
- **测试：** 从配置注册任务（调度器打桩/停用）；`run_scan` 用家庭时区 `today`；`environment=test` 下停用。
- **提交：** `feat(backend): daily scheduled reminder scan via APScheduler`

**Step 6 — in-app 通知 API**
- **构建：** `NotificationService` + 路由 `GET /notifications`、`GET /notifications/unread-count`、`POST /notifications/{id}/read`（404 `notification.not_found`）、`POST /notifications/read-all`；`NotificationResponse`/`UnreadCountResponse`；新错误码 `notification.not_found`；`make codegen`。
- **测试：** 列出最新在前 + 未读过滤；计数；标记一条/全部；非本人 id 404；按用户隔离。
- **提交：** `feat(backend): in-app notifications inbox API`

### Phase C — 外部通道

**Step 7 — 邮件（SMTP）摘要通道 + 服务端消息目录 + 投递日志**
- **构建：** 迁移 `0019`（`notification_deliveries`）；`NotificationDeliveryRepository`；`app/notifications/messages.py`（四个码 + 摘要外壳的双语服务端目录）；`EmailChannel`（标准库 `smtplib`、`use_tls`）；dispatcher **邮件 = 摘要**路径（按收件人、收件人语言）接入每日扫描；SMTP 配置已在设置（Step 1）。`make codegen`（若 delivery schema 暴露；否则无）。
- **测试：** 摘要按收件人分组 + 按收件人语言渲染；停用/未配置 = 空操作；SMTP 打桩（无真实发信）；投递行记录；幂等（已 sent 跳过）；迁移 `0019` 升/降。
- **提交：** `feat(backend): email digest notification channel`

**Step 8 — HTTP 通道（出站 webhook + 入站 HA 状态端点）**
- **构建：** `HttpChannel`（即时 `httpx` POST、可选 auth 头、基础 SSRF 备注）；`IntegrationStateService`；`GET /integrations/state`（token 鉴权，新错误码 `integration.invalid_token`）；集成 token 在设置（自动生成/重生成）。`make codegen`。
- **测试：** 启用时 webhook POST `{code,params,message}`、未启用时空操作（打桩 httpx）；状态计数与 service 一致；有效/无效 token（401）；投递行 + 幂等。
- **提交：** `feat(backend): HTTP webhook channel and Home Assistant state endpoint`

**Step 9 — MQTT 桥接：出站（发布 + 状态 + 发现）**
- **构建：** lifespan 中 `MqttBridge`/`MqttChannel`（paho-mqtt），由 `channels.mqtt.enabled` 开关；提醒发布、retained 状态主题、HA 发现配置（由 `discovery_enabled` 开关）；状态发布挂在扫描/事件后。**无 schema 变更 → 无 codegen。**
- **测试（paho 打桩）：** 连接/断开生命周期；提醒/状态/发现的主题+负载形状；retained 标志；停用时空操作。
- **提交：** `feat(backend): MQTT bridge outbound publish, state topics and HA discovery`

**Step 10 — MQTT 桥接：入站命令**
- **构建：** 订阅 `{prefix}/command/#`；把 `consume`/`intake`/`adjust` 映射到 `StockMovementService`，在新会话以系统执行者执行；发布命令结果；由 `commands_enabled` 开关（默认 false）；未知/畸形丢弃+记录。**无 schema 变更 → 无 codegen。**
- **测试（paho 打桩）：** 每条命令映射到正确 service 调用；畸形/未知丢弃（无改动、无抛出）；停用 = 忽略；结果发布。
- **提交：** `feat(backend): MQTT inbound command handling`

### Phase D — 前端

**Step 11 — 通知 bell + 收件箱 + /notifications 页**
- **构建：** `NotificationBell`（头部，未读角标轮询 + 从 `message_code`+`params` 本地化的下拉、全部已读、链接）；`pages/Notifications.tsx`（整列、未读过滤、标记已读、subject 链接）+ 路由 + 导航项；新 `notifications` 命名空间（en+zh）+ `errors` 键。
- **测试：** 角标计数；下拉本地化；标记已读清角标；页列表/过滤；导航。
- **提交：** `feat(frontend): notification bell, inbox and notifications page`

**Step 12 — Configuration 页 + 每条目/每用户 lead 字段**
- **构建：** `pages/Configuration.tsx`（提醒全局 + 每用户 + 通道 email/http/mqtt 带只写密钥 + 集成 token 控件 + "立即运行扫描"）+ 路由 + 导航项；definition 表单/详情上的 `reminder_lead_days`；profile/配置上的每用户 lead 字段；新 `configuration` 命名空间 + `items`/`nav` 补充（en+zh）。
- **测试：** 加载/编辑/掩码/PATCH 设置；运行扫描摘要；每条目与每用户 lead 字段；en+zh 完整。
- **提交：** `feat(frontend): configuration page for reminders and channels`

> 步骤过大时可拆（如 12 拆提醒 vs 通道，或 9 拆发布 vs 发现）；保持每步独立绿。Phase C 的 7–10 互相独立（任意子集可落/可发）；11 依赖 6，12 依赖 1/2/7/8/9。

---

## 10. 盲审检查点（逐步）

审查者**只**拿到：本文 + roadmap、本步实现简报、本步 diff。检查：

- **Step 1：** `settings` 是**面向用户**的 KV（与 `app_config` 区分）；默认在**代码**里（表只存覆盖）；密钥**只写**（响应里 `*_is_set`，永不回显）；校验经 Pydantic（无新错误码）；依赖 + AGENTS.md 更新；codegen 已提交。
- **Step 2：** 覆盖列可空 `≥0`，NULL = 继承；串接 definition + user(me) schema；非回溯（仅数据，无重算）；codegen 已提交。
- **Step 3：** 通知带 **`user_id` 收件人**；lead 链为 **每条目 > 每用户 > 全局**（第一个非空）；日期来源在 `today_local ≥ date − lead` 触发，**去重对 `(user_id, dedup_key)` 唯一**，重扫幂等；`today` 用 **household.timezone**；dispatcher 此处**仅 in-app**；codegen 已提交。
- **Step 4：** opener = episode 记录（无额外表）；复提醒用 `elapsed ≥ offset`（补偿）各一次；恢复盖 `resolved_at`；再次转低 = 新锚点/去重命名空间；事件钩子**同事务、尽力而为**，产出与扫描**一致**的行；无 schema/codegen 漂移。
- **Step 5：** 调度器**有开关**（测试中关）；cron 在 **household.timezone** 用 `scan_time`；引擎仍**直接**单测；无 codegen。
- **Step 6：** 收件箱端点限**当前用户**；非本人 id ⇒ `notification.not_found`（404）；响应带 **code+params、无服务端文本**；codegen 已提交。
- **Step 7：** 邮件为 **摘要、按收件人、收件人语言**，从**服务端目录**渲染（那条有界 wire/display 例外，合理）；停用 = 空操作；SMTP 测试中打桩；投递记录 + 幂等；迁移可逆。
- **Step 8：** webhook 负载 `{code,params,message}`；状态端点由**集成 token** 守卫（非会话 cookie），失败 `integration.invalid_token`；计数委托既有 service；SSRF 备注在场；codegen 已提交。
- **Step 9：** MQTT 桥接 **lifespan 管理 + 有开关**；状态主题 **retained**；发现有开关；测试中 paho 打桩；无真实 broker；无 codegen。
- **Step 10：** 命令集**有界**；经既有 service 执行（无新 SQL）；`commands_enabled` 默认 **false**；畸形/未知丢弃且无改动；无 codegen。
- **Step 11：** bell/收件箱从 **`message_code`+`params`** 本地化（不渲染服务端文本）；标记已读更新角标；en+zh 完整；只用有类型客户端。
- **Step 12：** Configuration 读写 `/settings`；UI 中密钥**只写**；"立即运行扫描"调 `/reminders/run`；每条目/每用户 lead 字段在场；en+zh 完整。
- **横切：** 符合 roadmap **§2.6（一个引擎、多来源、每条目+每用户 lead、事件+定时双保险、默认开、可插拔通道）**、§2.7（保修来源）、§2.9（Decimal/Date）、§2.10（单一 context/repository 层）、§2.11（逻辑在应用层不在 SQL）；M1.5 统一错误信封（只两个新码，无裸 `detail`）；本文变更时的**双语文档规则**。

---

## 11. 🟢 部署自测点（拼入 M4 里程碑走查）

作者在里程碑末跑的手动走查（展开 roadmap M4 🟢 条目）。假定 M1–M3 运行流程（compose up；以管理员登录；已有位置树、类别、若干库存）。

1. **配置 lead（全局）：** 在 **Configuration** 设 best-before lead = 3 天、warranty lead = 30、低库存复提醒 = `1,3,7`、扫描时间 = `08:00`。保存（重载显示该值）。
2. **覆盖：** 给某条目设**每条目** `reminder_lead_days`（如护照 = 90）；给当前账户设**每用户** best-before lead（如 1）。保存。
3. **播种信号：** 登记一个 3 天内到期的生鲜批次；一个 `warranty_expires` 在 30 天内的耐用批次；把一个消耗品消耗到 `min_stock` 以下。
4. **即时低库存（事件）：** 消耗跌破阈值的瞬间，一条**低库存通知**出现在 bell 里，**无需**运行扫描。
5. **运行扫描：** 点 **立即运行扫描**（或 `POST /api/reminders/run`）→ 一**组合并**的 in-app 通知出现：best-before、warranty、低库存 —— 各自本地化（切 ZH⇄EN，收件箱跟随）。重跑 → **无重复**（幂等）。
6. **邮件摘要：** 配好 SMTP（测试 catcher / Mailpit）后，扫描发**一封摘要邮件**给管理员覆盖三者，用管理员语言。
7. **HTTP：** 设了 webhook URL 后，提醒**POST**到它；`GET /api/integrations/state?token=…` 返回实时计数（错/缺 token ⇒ 401）。
8. **MQTT（完整桥接）：** 配好 broker 后，**状态主题**发布计数、**提醒事件**逐条发布；**Home Assistant** 自动发现传感器（HA 中可见计数）；向 `{prefix}/command/consume` 发一条 `consume` **命令**会减库存并发布结果（当 `commands_enabled`）。
9. **复提醒节奏：** 让低库存不补货；推进时钟（或跨日重跑）按配置 offset 触发**复提醒**，各一次 —— 非每天。
10. **episode 关闭：** 补货到 `min_stock` 以上，运行扫描 → episode **关闭**（`resolved_at`），不再复提醒；再次转低开启**新** episode。
11. **CI 绿：** 同样的门禁在 GitHub Actions 通过，包括无漂移契约门禁；迁移 `0001`–`0019` 在 docker 冒烟里干净应用于全新 DB。

---

## 12. 开放问题 / 推迟

- **每条目 vs 每用户优先级：** M4 锁定 **每条目 > 每用户 > 全局**（第一个非空）。若实际使用显示覆盖语义出人意料，"**取适用者最大**"（任一策略最早会提醒时就提醒）是可选替代 —— 再议。
- **日期来源再触发：** M4 在 lead 边界**触发一次**。在**当天**和/或**新逾期**时再触发（第二条升级通知）是记录的细化 —— 与"每来源每条目 lead"配套。
- **复提醒节奏默认：** `[1, 3, 7]` 是可调默认（作者曾设想 1/3/5/7 但"太频繁不好"）；在设置里改。**退避**节奏（逐渐拉大间隔）是候选细化。
- **每用户通道 + 低库存偏好：** M4 把每用户限于 **lead time**。每用户**通道**选择（"邮件提醒我但不要 MQTT"）和每用户**低库存**节奏推迟（与 M6 多用户 + 角色配套）。
- **责任人路由：** M4 扇出到**所有 active 用户**。收窄到**指派的责任人** + 角色可见的收件箱属 **M6**（`notifications.user_id` 接缝已就位）。
- **调度器：多 worker + 实时改期：** 假定单 uvicorn worker（无分布式锁）；`scan_time` 变更在**重启**时（或经按需端点）生效 —— 实时重注册 + 多 worker 锁是部署扩容时的细化。
- **通道安全强化：** 出站 webhook 的 **SSRF 守卫**与**限流**属 **M6**（roadmap §5 M6）；M4 做基础公网 URL 合理性检查。MQTT broker 由**运维信任**；`commands_enabled` 默认 **false**；更严的命令鉴权/白名单是后续。
- **episode 模型单表：** opener-行-即-episode（无专门 `episodes` 表）对单一状态来源是刻意的。若维护到期（M7）或未来状态来源需更丰富的 episode 状态，提升为独立表是受控重构。
- **通知保留：** M4 保留所有通知行（家庭规模）。保留/自动清理策略（如已读+已解决 N 天后归档）是后续细化。
- **摘要 vs 逐条邮件：** M4 让邮件为**每日摘要**；"每条提醒即时一封邮件"选项（每用户偏好）在每用户通道偏好落地时再议。
