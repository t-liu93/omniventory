# M1 — 统一核心模型与耐用品台账(②)

> 🌐 **语言:** [English](./M1.md) · 中文(当前)

> **里程碑设计文档 — 自包含。** 与 `docs/plan/roadmap.md`(地图)一起阅读;「我们为何存在」见 `docs/inspiration/investigation.md`。本文是「M1 建什么、怎么验证」的唯一真相源;不要从 roadmap 反推范围。进度**只**记录在 roadmap §4 表格里。
>
> 内建约定:原子步骤(§9)、盲审检查点(§10)、🟢 部署自测点(§11),让手动与编排两种执行模式都能挂在本文上。

---

## 1. 目标与非目标

**目标(roadmap 对 M1 的承诺):** 在真实、可嵌套的位置层级里**登记并浏览耐用品**。M1 是**「定义 / 实例分离」**这条脊椎(roadmap §2.1)——整个项目都挂在它上面——**第一次落进 schema** 的里程碑。后续每个里程碑(M2 流水、M3 保质期、M4 提醒)都在 M1 建的表上扩展。

**完成判据(🟢,§11 展开):** 能创建嵌套的位置树;往里登记一件带序列号的耐用品(含序列号 / 型号 / 制造商 / 保修 / 购入价值);能编辑;能搜索;能把一个「容器」位置标记为「这个地点本身就是一件被追踪的资产」(容器即物品);并且 `serial ⇒ quantity = 1` 约束在 **API 层和 DB 层**都能拒绝非法输入。CI 保持绿,包含无漂移契约门禁。

**非目标(明确排除在 M1 之外 —— 属于后续里程碑):**
- **没有数量流水 ledger。** M1 的 `quantity` 是直接存储的列;类型化的出入库/移动/调整流水、以及「数量由流水推导」在 **M2**(M2 会为每个已有实例回填一条初始入库 movement —— 见 §2 决策 Q2)。
- **没有保质期 / best-before。** `best_before_date` 与 `default_best_before_days`(入库自动推算)是 **M3**。
- **没有提醒。** `warranty_expires` 在 M1 **只存不扫描**;统一提醒引擎是 **M4**。
- **没有横切能力。** 照片 / 附件、标签、备注、自定义字段、条码都是 **M5** —— 所以 **M1 的耐用品没有照片**。
- **没有多单位换算。** 定义只带单一 `unit` 字符串;采购/库存/消耗单位 + 换算系数是 **M8**。
- **没有最低库存 / 低库存。** `min_stock` 在 **M2**(其消费方)落到定义上,不在现在。
- **没有 M0 之外的多用户 / 角色。** M1 所有接口都要求有效会话(那个唯一 admin);角色 / 权限是 **M6**。

---

## 2. 已锁定决策(M1 规划中敲定;理据见 roadmap §2/§3 + investigation 第三章)

| 方面 | 决策 |
|---|---|
| **脊椎** | **定义 / 实例分离**(InvenTree `Part`/`StockItem`)。`item_definitions` = 「这是什么品类」(名称、分类、kind、单位、默认值)。`stock_instances` = 「具体这一份/这一件」(数量、位置、序列号、保修、价值)。绝不合并(roadmap §2.1)。 |
| **序列号约束** | **`serial ⇒ quantity = 1`**,在 **DB**(`CHECK (serial IS NULL OR quantity = 1)`)与**应用服务层**双重强制(roadmap §2.2)。一张实例表同时容纳「逐件耐用品」与(将来的)「成批耗材」。 |
| **容器即物品**(Q1) | **桥接,不合并。** `locations` 是独立的自引用树;`locations.item_instance_id`(**可空、唯一 FK → stock_instances**)表达「这个位置物理上就是那件被追踪的耐用品」(工具箱)。保持两层模型干净;容器即物品是*加挂*,而非 Homebox 式单表合并。 |
| **M1 的数量**(Q2) | **直接存**为 `Numeric` 列(默认 `1`)。ledger 尚不存在(M2)。M2 落地时,`quantity` 改为**由流水推导**,并由 M2 迁移为每个已有实例回填一条等于当前数量的 `intake` movement。M1 **不**为还不存在的 ledger 过度设计。 |
| **Decimal 与货币** | `quantity` 与 `purchase_price` 用 SQLAlchemy `Numeric`(→ Python `Decimal`),绝不用 float(roadmap §2.9)。货币**继承 `household.currency`**;实例在 v1 **不**存逐条币种。M1 不发生货币运算,舍入规则推迟到首次用货币计算的里程碑。 |
| **定义默认值的时机** | `min_stock`(M2)与 `default_best_before_days`(M3)由**消费它们的里程碑**各自通过迁移添加。M1 的定义只带 M1 自己用到的字段。1.0 前无数据,后加一列很便宜,且让每个里程碑的 schema 变更内聚。 |
| **`kind`**(作者拍板) | 一张 **`item_kinds` 查找表的 FK**,*而非*写死的字符串枚举 —— 现在(M1)就定,这样以后被 FK 引用时不需要破坏性契约变更。M1 保持**最小**:种子 3 个系统 kind(`durable` / `consumable` / `perishable`),**只读**暴露(`GET /kinds`);定义的 `kind_id` 省略时默认解析为 `durable`。仅作 UI 提示 + 默认值驱动 —— **暂无硬业务逻辑**分支于它。kinds 的增删改 + 每种行为开关推迟(§12)。 |
| **`unit`** | 定义上单一自由文本单位串(如 `pcs`、`m`、`kg`),默认 `pcs`。多单位换算是 M8。 |
| **搜索** | 最简:列表接口接受 `q` 查询参数,对相关文本列做**大小写不敏感子串**匹配(定义名;实例 serial / model / manufacturer)。无全文索引。 |
| **序列号唯一性** | 在 `(definition_id, serial) WHERE serial IS NOT NULL` 上建**部分唯一索引** —— 同一物理序列号不能在同一品类下登记两次。(拒绝全局唯一:两个不同品类可能合法共用同一序列号串。)§12 列出待复议。 |
| **树删除语义** | 删除位置/分类时,若仍有子节点 → **阻止(HTTP 409)**;位置若有实例归属或被作为容器引用(`item_instance_id`)也阻止。M1 不级联 —— 安全默认,避免产生孤儿。 |
| **认证** | **M1 每个业务接口都要求有效会话**(经 `get_authenticated_context` / `get_current_user`,M0 已有的依赖)。无公开业务接口。 |
| **前端路由** | 引入 **`react-router-dom`**(M0 故意没上路由)。M0 的 `App.tsx` auth gate 仍是外层壳;路由活在已认证的 `AppShell` *内部*。树形 UI 用 Mantine 的 **`Tree`** 组件。 |

> 这些是对 M0 基础的扩展,**不是**「技术栈」变更,所以 M1 **不改** `AGENTS.md` —— 唯一例外是 `react-router-dom` 确属新增前端依赖(在 M1 报告里记一笔;若作者想让技术栈清单详尽,可在里程碑收尾时给 AGENTS「技术栈」行补一个词)。

---

## 3. 数据模型(M1 的核心)

五张新表。全部沿用 M0 约定:SQLAlchemy 2.0 类型化 `Mapped[...]` 挂在共享 `DeclarativeBase` 上,`created_at` 用 `server_default=func.now()`,只经 repository 访问(路由里无原始查询),业务规则放 service 层。

### 3.1 `locations` —— 自引用树(+ 容器即物品桥接)

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer PK | 否 | 自增 |
| `name` | String(255) | 否 | |
| `description` | String(1000) | 是 | |
| `parent_id` | FK → `locations.id` | 是 | `NULL` = 根节点;任意深度 |
| `item_instance_id` | FK → `stock_instances.id` | 是 | **唯一**;这个位置*就是*哪件耐用品实例(容器即物品)。**在步骤 4 添加**(见 §3.6)。 |
| `created_at` | DateTime(tz) | 否 | `server_default=func.now()` |

- **防环**(应用层,roadmap §2.11 —— 不用 SQL 触发器):改父时,新父不能是节点自身,也不能是它的任何后代。
- 位置自身在世界中的位置是它的 `parent_id`;工具箱实例本身则经 `stock_instances.location_id` 落在某个位置里。桥接(`item_instance_id`)是反向链接。两者建模的是不同关系(容纳 vs 身份),不会成环。

### 3.2 `categories` —— 自引用树

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer PK | 否 | |
| `name` | String(255) | 否 | |
| `description` | String(1000) | 是 | |
| `parent_id` | FK → `categories.id` | 是 | `NULL` = 根;任意深度 |
| `created_at` | DateTime(tz) | 否 | |

与位置相同的防环 + 删除守卫规则(复用步骤 1 的树形范式)。

### 3.3 `item_kinds` —— kind 查找表(种子)

一张小引用表,让 `kind` 是 **FK 而非写死的字符串枚举** —— 现在(M1)就做,这样以后被引用时不必破坏性契约变更。M1 保持**最小**:种子 3 个系统 kind,API 只读暴露;用户自管的 kinds 增删改、以及每种 kind 的行为开关(如「perishable 才有保质期」)都推迟(§12)。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer PK | 否 | |
| `code` | String(32) | 否 | **唯一**;稳定机器键(`durable` / `consumable` / `perishable`) |
| `name` | String(64) | 否 | 显示名 |
| `is_system` | Boolean | 否 | 默认 `true`;种子系统 kind(给未来用户 kind 留位) |
| `created_at` | DateTime(tz) | 否 | |

由其迁移种子(M0 的 `INSERT OR IGNORE` 范式)写入 `durable` / `consumable` / `perishable`(全部 `is_system = true`)。

### 3.4 `item_definitions` —— 「这是什么品类」

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer PK | 否 | |
| `name` | String(255) | 否 | |
| `description` | String(1000) | 是 | |
| `category_id` | FK → `categories.id` | 是 | |
| `kind_id` | FK → `item_kinds.id` | 否 | 省略时 service 解析为 `durable` kind |
| `unit` | String(32) | 否 | 默认 `pcs` |
| `default_location_id` | FK → `locations.id` | 是 | 新建实例的建议位置 |
| `created_at` | DateTime(tz) | 否 | |

> `min_stock`(M2)与 `default_best_before_days`(M3)有意**缺席** —— 由其消费里程碑添加。

### 3.5 `stock_instances` —— 「具体这一份 / 这一件」

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer PK | 否 | |
| `definition_id` | FK → `item_definitions.id` | 否 | 这个实例是什么 |
| `location_id` | FK → `locations.id` | 是 | 物理在哪 |
| `quantity` | Numeric(18,6) | 否 | 默认 `1`;**M1 直接存**(M2 改为流水推导) |
| `serial` | String(255) | 是 | |
| `model_number` | String(255) | 是 | 耐用品身份 |
| `manufacturer` | String(255) | 是 | 耐用品身份 |
| `warranty_expires` | Date | 是 | **只存**;提醒是 M4 |
| `warranty_details` | String(1000) | 是 | |
| `purchase_price` | Numeric(18,2) | 是 | 价值;币种 = `household.currency` |
| `purchase_date` | Date | 是 | |
| `purchase_source` | String(255) | 是 | 从哪买的 |
| `created_at` | DateTime(tz) | 否 | |

约束(DB 层,schema 不变式 —— roadmap §2.1/§2.11 允许):
- **`CHECK (serial IS NULL OR quantity = 1)`** —— 序列号约束,同时在 service 层强制。
- **部分唯一索引** `(definition_id, serial) WHERE serial IS NOT NULL`。

> **身份 vs 唯一性(重要)。** 表的主键仍是代理键 `id`;`(definition_id, serial)` 只是一个*唯一性约束*,**不是**复合主键 —— `serial` 可空(成批 lot 没有,而主键列不能为 NULL),且一个定义可以合法持有**很多**条无序列号的批次/lot(这正是定义/实例分离的意义)。所以这一对唯一标识一件*带序列号*的单品,而无序列号的成批实例是有意"一个定义多条",靠 `id` 区分。

### 3.6 交叉 FK 的顺序问题(为何桥接落在步骤 4)

`locations.item_instance_id → stock_instances.id` 与 `stock_instances.location_id → locations.id` 互相引用。迁移顺序:

1. **步骤 1(迁移 0004)** 建 `locations`,**不含** `item_instance_id`(此时实例尚不存在)。
2. **步骤 4(迁移 0008)** 建 `stock_instances`(它的 `location_id` FK 有效 —— `locations` 已存在),然后用 Alembic **batch 模式**(`with op.batch_alter_table("locations") as batch: ...`)**添加 `locations.item_instance_id` + 其 FK**,因为 SQLite 不能直接 `ALTER TABLE ... ADD CONSTRAINT`(batch 模式会重建表)。

这也契合领域:没有任何实例之前,无所谓「把位置链接到实例」。

### 3.7 迁移清单

| 版本 | 步骤 | 创建 |
|---|---|---|
| `0004` | 1 | `locations`(仅树) |
| `0005` | 2 | `categories` |
| `0006` | 3 | `item_kinds`(种子) |
| `0007` | 3 | `item_definitions` |
| `0008` | 4 | `stock_instances` + batch-alter `locations` 加 `item_instance_id` FK |

全部可逆(downgrade 逆序删;0008 的 downgrade 先 batch 删列,再删表)。

---

## 4. 后端设计

### 4.1 Repository + service 分层(扩展 M0)

- **Repositories**(`app/repositories/`):每表一个 —— `LocationRepository`、`CategoryRepository`、`ItemKindRepository`(只读:`list` + `get_by_code` 供默认解析)、`ItemDefinitionRepository`、`StockInstanceRepository`。纯数据访问(get / list / create / update / delete + 树特有读取如 `get_children`、`get_descendants`)。这里**无业务规则**;**路由里无原始查询**。
- **Services**(`app/services/` —— 该层首次真正启用):承载易错逻辑:
  - `TreeService`(位置 + 分类共用的基类或 mixin):改父防环;删除守卫(阻止非空);构建嵌套树 DTO。
  - `LocationService`:树逻辑 + 容器即物品链接(设/清 `item_instance_id`,强制其唯一性、强制目标实例存在)。
  - `StockInstanceService`:强制 `serial ⇒ quantity = 1`;`quantity` 默认 1;新建实例未给位置时从定义的 `default_location_id` 解析。
- 路由依赖 `get_authenticated_context`(携带 household + user),调用 service/repository。`RequestContext` 是未来多租户 `household_id` scope 的接缝。

### 4.2 API 面(全部挂在 `settings.api_prefix`,默认 `/api`;全部需认证)

每个资源:`GET`(列表,带过滤/搜索)、`POST`(创建)、`GET /{id}`、`PATCH /{id}`、`DELETE /{id}`。树额外暴露嵌套读取。

| 方法 + 路径 | 用途 |
|---|---|
| `GET /locations?q=&parent_id=` · `GET /locations/tree` | 扁平列表 / 过滤 / 完整嵌套树 |
| `POST/GET/PATCH/DELETE /locations[/{id}]` | CRUD(删除带守卫);`PATCH` 可设 `parent_id`(改父,查环)与 `item_instance_id`(容器即物品) |
| `GET /categories?q=&parent_id=` · `GET /categories/tree` · CRUD | 分类树 |
| `GET /kinds` | **只读**列出种子 kind(给定义的 kind 选择器用);M1 无写端点 |
| `GET /definitions?q=&category_id=` · CRUD | 物品定义 |
| `GET /instances?q=&definition_id=&location_id=` · CRUD | 库存实例;`serial⇒qty=1` 以 422 拒绝 |

错误契约:`409` 用于删除守卫 / 成环 / 唯一性冲突;`422` 用于 `serial⇒qty=1` 与校验;`404` 用于不存在的 id;`401` 用于无会话。

### 4.3 Pydantic schemas(`app/schemas/`)

每资源薄薄的线格式 DTO:`*Create`、`*Update`(PATCH 用,全可选)、`*Response`,以及 `*TreeNode`(递归,用于嵌套树读取)。`from_attributes = True`,如 M0 的 `UserResponse`。`item_kinds` 只给 `KindResponse`(只读 —— 无 `Create`/`Update`)。

---

## 5. 质量门禁与易错逻辑(完成定义)

继承 M0 §5(`make check` = ruff + mypy + pytest + eslint + tsc + vitest 全绿;build 通过)。M1 中**必须有单元测试**的逻辑(roadmap DoD):

- **树防环**:把节点改到自身或后代之下被拒绝。
- **树删除守卫**:删非空位置/分类 → 409;删有实例归属或有容器链接的位置 → 409。
- **`serial ⇒ quantity = 1`**:在 **service 层**(422)拒绝 *并* 在 **DB 层**证明(`CHECK` 对直接的非法插入抛 `IntegrityError`)。
- **序列号部分唯一**:重复的 `(definition_id, serial)` 被拒;同一序列号在*不同*定义下允许;两个 `NULL` 序列号共存。
- **容器即物品完整性**:`item_instance_id` 唯一(一实例 ↔ 一位置);链接不存在的实例失败。
- **默认位置解析**:新建实例不给位置时回退到定义的 `default_location_id`(定义没有则保持 `NULL`)。
- **默认 kind 解析**:新建定义不给 `kind_id` 时回退到种子的 `durable` kind;非法 `kind_id` 被拒。
- **kind 种子**:`item_kinds` 迁移 `upgrade head` 后恰好留下三个系统 kind(`durable` / `consumable` / `perishable`)。
- **迁移往返**:`0004`–`0008` 在空 DB 上 upgrade 干净、downgrade 干净(含 0008 对 `locations` 的 batch-alter)。

---

## 6. 契约优先 codegen 与无漂移门禁

机制与 M0 §6 一致:每个动了 API 的步骤都**重跑 `make codegen`**(重新生成仓库根 `openapi.json` + `frontend/src/api/schema.d.ts`)并提交两者。CI **contract** job(`make codegen` + `git diff --exit-code`)在漂移时失败。M1 只是用新资源扩大 schema;前端经现有 `openapi-fetch` 客户端消费新的类型化路径。

---

## 7. 前端设计

### 7.1 路由(新)

- 加 **`react-router-dom`**。M0 的 `App.tsx` gate(setup → login → authed)仍是**外层**决策;当 `authed` 时,在 `AppShell` 内渲染 `<BrowserRouter>`。
- 路由:`/`(仪表盘占位)、`/locations`、`/categories`、`/items`(定义列表)、`/items/:id`(定义详情 + 其实例)、`/instances/:id`(实例详情)。导航链接放在现有的 `AppShell` 侧栏/抽屉里。

### 7.2 树浏览

- 位置与分类用 Mantine **`Tree`**:展开/折叠、选中节点查看/编辑、建子 / 重命名 / 改父 / 删除(删非空时显示 409 守卫提示)。
- 位置节点在「容器即物品」时可见地标示(链接到实例)。

### 7.3 定义与实例

- **定义**:列表(带 `q` 搜索 + 分类过滤)、新建/编辑表单(名称、描述、分类选择器、kind、单位、默认位置)、详情页列出该定义的实例。
- **实例**:新建/编辑表单(定义选择器、位置选择器、数量、序列号、型号、制造商、warranty_expires、warranty_details、购入价/日期/来源)。**客户端 `serial ⇒ quantity = 1`** 校验(输入序列号时禁用/自动把数量设为 1)**外加**兜底显示服务端 422。按 `q` 搜索。

### 7.4 测试(vitest + Testing Library)

树渲染 + 节点 CRUD(mock client);定义表单提交;实例 `serial⇒qty=1` 客户端规则;列表搜索渲染;路由渲染到正确页面。沿用 M0「mock 类型化 client」的风格。

---

## 8. CI & Docker(相对 M0 的增量)

无结构变化。**docker** 冒烟测试的迁移步骤现在对全新 bind-mount DB 应用 `0001`–`0008`(原为 `0001`–`0003`)。其余(缓存、契约门禁、单镜像、fail-closed 的 `migrate` 服务)与 M0 §8 完全一致。

---

## 9. 步骤拆分(原子、有序)

每步可独立测试、有测试背书、恰好落一个提交(编排模式下每步一次 autosquash),动了 API 就重跑 `make codegen`,并继承全局 DoD(§5)+ **反夹带规则**:只实现*本步* —— 不做其它步、不顺手重构、不碰宿主真实环境。

> **排序理据:** FK 决定顺序 —— `categories`/`locations`/`item_kinds` 先于 `item_definitions`(它 FK 三者)先于 `stock_instances`(FK 定义 + 位置);容器即物品桥接等 `stock_instances`(§3.6)。后端(1–4)落下类型化契约,前端(5–6)消费它。

### 步骤 1 —— 位置自引用树(后端)
- **目标:** 带 CRUD、防环、删除守卫的嵌套位置树 —— 确立可复用的**树形 repo/service 范式**。
- **构建:** `models/location.py`(`id, name, description, parent_id, created_at` —— **暂无** `item_instance_id`);迁移 `0004`;`LocationRepository` + 树感知的 `LocationService`(改父查环、删除守卫);`schemas/location.py`(`Create/Update/Response/TreeNode`);`api/routes/locations.py`(CRUD + `/tree` + `?q=&parent_id=`);注册路由;`make codegen`。
- **测试:** 防环被拒(自身 + 后代);改父 OK;非空删除 409;树 DTO 形状;`q` 搜索;迁移 0004 up/down。
- **不在范围:** 无容器即物品桥接(步骤 4);无其它表。
- **提交:** `feat(backend): location self-referential tree with CRUD`

### 步骤 2 —— 分类自引用树(后端)
- **目标:** 分类树,复用步骤 1 的树形范式。
- **构建:** `models/category.py`;迁移 `0005`;`CategoryRepository` + service(经共享树逻辑做防环 + 删除守卫);`schemas/category.py`;`api/routes/categories.py`(CRUD + `/tree` + `?q=&parent_id=`);`make codegen`。
- **测试:** 镜像步骤 1(防环、删除守卫、树、搜索、迁移)。
- **不在范围:** 定义/实例。
- **提交:** `feat(backend): category self-referential tree with CRUD`

### 步骤 3 —— kind 查找表 + 物品定义(后端)
- **目标:** 种子化的 `item_kinds` 查找表 +「这是什么品类」CRUD。
- **构建:**
  - `models/item_kind.py`(`id, code 唯一, name, is_system, created_at`);迁移 `0006`(建 `item_kinds` 并用 `INSERT OR IGNORE` **种子** `durable`/`consumable`/`perishable`);`ItemKindRepository`(只读 `list` + `get_by_code`);`schemas/item_kind.py`(只有 `KindResponse`);`api/routes/kinds.py`(**`GET /kinds`** 只读)。
  - `models/item_definition.py`(`name, description, category_id, kind_id FK, unit, default_location_id, created_at`);迁移 `0007`;`ItemDefinitionRepository` + service(`kind_id` 省略时解析 `durable` kind);`schemas/item_definition.py`;`api/routes/definitions.py`(CRUD + `?q=&category_id=`);`make codegen`。
- **测试:** `GET /kinds` 恰好返回三个种子 kind;定义增删改查;`kind_id` 省略时默认解析;非法 `kind_id` 被拒;FK 到分类/位置;`q` 搜索;迁移 0006(含种子)+ 0007 up/down。
- **不在范围:** `min_stock`/`default_best_before_days`(M2/M3);kinds 增删改 / 行为开关(推迟 §12);实例。
- **提交:** `feat(backend): item kinds lookup and item definition CRUD`

### 步骤 4 —— 库存实例 + 容器即物品桥接(后端)
- **目标:**「具体这一件/这一份」CRUD,带序列号约束,并接通容器即物品。
- **构建:** `models/stock_instance.py`(§3.5 全部字段;`CHECK(serial IS NULL OR quantity=1)`;部分唯一 `(definition_id, serial)`);迁移 `0008`(建 `stock_instances`,再 **batch-alter `locations`** 加 `item_instance_id` FK,唯一);给 `Location` 模型加 `item_instance_id`;`StockInstanceRepository`;`StockInstanceService`(`serial⇒qty=1`、默认位置解析);扩展 `LocationService` 加容器即物品链接/解链;`schemas/stock_instance.py`;`api/routes/instances.py`(CRUD + `?q=&definition_id=&location_id=`);扩展位置路由/schema 以设 `item_instance_id`;`make codegen`。
- **测试(易错逻辑 —— 必须):** `serial⇒qty=1` 在 **service(422)** 与 **DB(IntegrityError)** 双层被拒;部分唯一(重复被拒、跨定义允许、NULL 共存);默认位置回退;容器即物品唯一性 + 链接不存在实例失败 + 被链接/被占用时位置删除守卫;迁移 0008 up/down 含 batch-alter。
- **不在范围:** 无 ledger/数量推导(M2);无 best_before(M3);无保修扫描(M4)。
- **提交:** `feat(backend): stock instance registry with serial⇒qty=1 and container-as-item`

### 步骤 5 —— 前端基础:路由 + 导航 + 树浏览
- **目标:** 路由 + 位置与分类的树 UI。
- **构建:** 加 `react-router-dom`;在已认证 `AppShell` 内挂 `<BrowserRouter>`;真实导航链接;`pages/Locations.tsx` + `pages/Categories.tsx` 用 Mantine `Tree`(浏览 + 建子 / 重命名 / 改父 / 删除带 409 提示);展示位置的容器即物品链接。
- **测试:** 树渲染 + 节点 CRUD(mock client);路由展示正确页面;删除守卫提示渲染。
- **不在范围:** 定义/实例页(步骤 6)。
- **提交:** `feat(frontend): router, nav, and location/category tree browse`

### 步骤 6 —— 前端:定义 + 实例(列表 / 详情 / CRUD / 搜索)
- **目标:** 在 UI 里端到端登记并浏览耐用品。
- **构建:** `pages/Items.tsx`(定义列表 + `q` + 分类过滤)、定义新建/编辑表单 + 详情(列出其实例);实例新建/编辑表单带**客户端 `serial⇒qty=1`** + 服务端 422 兜底;实例详情;搜索。
- **测试:** 定义表单提交;实例 `serial⇒qty=1` 客户端规则;列表搜索;实例详情渲染(mock client)。
- **不在范围:** 照片/标签/自定义字段(M5)。
- **提交:** `feat(frontend): durable definitions and instances with search`

> 若步骤 6 过大,可拆成 **6a(定义)** / **6b(实例)**;各自保持独立绿。

---

## 10. 盲审检查点(逐步)

审查者**只**拿到:本文 + roadmap、该步实现简报、该步 diff。检查:

- **步骤 1:** 防环在 **service 层**(无 SQL 触发器);删除守卫阻止非空;路由无原始查询;迁移可逆;`/tree` DTO 正确;codegen 已提交。
- **步骤 2:** 真正复用步骤 1 的树形范式(无复制粘贴走样);相同守卫成立。
- **步骤 3:** `item_kinds` 恰好种子三个系统 kind;`kind_id` 是真 FK(无残留字符串枚举/CHECK);`GET /kinds` 只读(无写端点);默认 kind 解析可用;其它 FK 正确;`min_stock`/`default_best_before_days` **缺席**(未提前夹带);codegen 已提交。
- **步骤 4:** `serial⇒qty=1` 在 **DB `CHECK`** 与 **service** 双层;部分唯一索引正确;`locations` 的 FK 用了 **batch-alter**(无坏掉的 SQLite ALTER);容器即物品唯一性强制;quantity 是**普通存储列**(无提前 ledger);codegen 已提交。
- **步骤 5:** 路由挂在已认证壳**内部**(M0 gate 完好);单一树组件复用;cookie-only 认证不变(localStorage 无东西)。
- **步骤 6:** 客户端 `serial⇒qty=1` **与** 服务端 422 都处理;搜索接通;用类型化 client(无手写 fetch)。
- **横切:** 符合 roadmap §2(尤其 §2.1 分离、§2.2 序列号、§2.5 位置树 + 容器即物品、§2.9 Decimal、§2.11 逻辑在应用层而非 SQL);本文变更时遵守双语文档规则。

---

## 11. 🟢 部署自测点(拼进 M1 里程碑走查)

作者在里程碑末尾跑的手动走查(展开 roadmap M1 🟢 条目)。假设已走 M0 的运行流程(compose up;以 admin 登录):

1. **嵌套位置树:** 建 `Home → Garage → Toolbox` 与 `Home → Kitchen`。树嵌套渲染;把 `Kitchen` 改父到 `Garage` 成功;试图把 `Garage` 改父到 `Toolbox`(其后代)被**拒绝**。
2. **分类树:** 建 `Tools → Power tools` 与 `Electronics`。
3. **登记带序列号的耐用品:** 建定义 `Cordless Drill`(kind 从种子列表里选 = durable,unit=pcs,分类=Power tools);登记一个实例到 `Garage`,带序列号、型号、制造商、`warranty_expires`、购入价/日期。它出现在列表与详情。
4. **`serial ⇒ qty = 1` 拒绝非法输入:** 在已设序列号的情况下把该实例改成 `quantity = 3` 被**拒绝**(UI 里 422);DB `CHECK` 独立阻止直接的非法写入。
5. **容器即物品:** 把 `Toolbox` *位置*标记为被追踪实例「Toolbox」(一件有自身价值/保修的耐用品)。该位置现在链接到那个实例;当 `Toolbox` 位置持有物品或被链接时删除它被**阻止(409)**。
6. **编辑与搜索:** 编辑钻的制造商;搜索 `q=drill`(以及按序列号)能返回它。
7. **CI 绿:** 同样的门禁在 GitHub Actions 通过,含无漂移契约门禁;docker 冒烟测试里迁移 `0001`–`0008` 对全新 DB 干净应用。

---

## 12. 待定问题 / 推迟

- **序列号唯一性范围:** M1 用**按定义**的部分唯一 `(definition_id, serial)`;主键仍是代理键 `id`(见 §3.5)。若哪天想要跨品类的全局序列号注册表再复议(家庭场景不太可能)。
- **树删除语义(已确认:未来要支持删除已占用节点):** M1 **阻止**删除非空节点(安全默认)。未来**会**支持删除已占用的节点 —— 但选定方向是**软删 / 归档** 和/或 **带确认的级联**(先告知"将删除 N 件被追踪资产"),**而非**静默的硬级联,因为级联删一个位置会抹掉里面所有资产。随 M5/M6 复议。
- **定性库存等级(前瞻需求 —— 影响 M2 设计):** 有些物品(尤其是小零件)无法精确计数,只能标 `high` / `medium` / `low`。规划 M2 时给**定义**设计一个*库存追踪方式*:`exact`(Decimal + M2 流水;低库存 = 低于 `min_stock`)· `level`(定性枚举,手动设,**不走流水**;低库存 = 等级为 `low`)· `none`(只记有无,不记数量)。M2 流水只对 `exact` 模式生效。**M1 不用改** —— `quantity` 仍是普通存储列;这个列/模式在 M2 添加。
- **M1 里非序列号成批的 `quantity`:** 允许(直接存),但无 ledger 时该值只是个存储数字,直到 M2 让它由流水推导(对 `exact` 模式的定义)。规划 M2 时确认其回填迁移设计。
- **Kind 表的可扩展性:** M1 种子三个**只读**系统 kind。推迟:用户自管的 kinds 增删改,以及**每种 kind 的行为开关**(如「perishable 才有保质期 / 默认货架天数」「durable 才显示保修」)—— 大概率在 M3/M5 想要「按 kind 驱动字段可见性」时,作为 `item_kinds` 上的列添加。FK 现在已就位,所以这些以后都不需要破坏性契约变更。
- **移动端导航形态**(自 M0 §12 带过来):既然 M1 引入了真实顶层分区,决定 `Drawer` vs 底部 tab bar。
- **`react-feather` 覆盖度** 对新动作(树展开、加子、改父)—— 步骤 5 时确认;有缺口就标记。
- **`AGENTS.md` 技术栈行:** 若作者想让技术栈清单详尽,里程碑收尾时加上 `react-router-dom`。
