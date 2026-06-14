# 库存系统调研报告:Homebox / InvenTree / Grocy

> 用途:本报告将被复制进一个全新自研「三合一库存系统」项目的文档,作为其「立项来历 / 灵感与洞察」。
> **自包含承诺**:读完本报告无需再打开任何参考项目源码——三份单项目简报全文已内联(见第二章)。

---

## 调研元信息

- **调研日期**:2026-06-13
- **调研方式**:编排者派出三个独立子代理,每个**只读一个项目目录**(隔离上下文),以源码(ORM/模型、数据库迁移、API、后台任务)为真相,文档仅作辅助并逐条核验。
- **调研对象与版本/commit**:

| 项目 | 版本(标称) | 实际检出 commit | 自报版本(代码核验) | License |
|---|---|---|---|---|
| **Homebox** | v0.26.1 | `1b40a9ef` (2026-06-13) | v0.26.1 | AGPL-3.0 |
| **InvenTree** | 标称 0.13.0(+约3000 commit) | `457fe16f9` (2026-06-13) | **实为 1.4.0-dev / API v503** | MIT |
| **Grocy** | v4.6.0 | `c0a9d615` (v4.6.0 + 26 commit) | v4.6.0 | MIT |

- **重要版本提示(影响结论可信度)**:
  - **InvenTree**:委托方给的基线是 0.13.0(2023 年),但仓库实际检出是 **1.4.0-dev 开发主线**(2026 年中)。本报告所述若干关键能力(尤其「临期主动提醒 `check_stale_stock` 每日定时任务」)是 0.13.0 之后才加入的。**若委托方真锁 0.13.0,这些能力可能不存在或形态不同**,需自行核对。
  - **Homebox**:本 v0.26.1 的数据模型与网上常见的「旧版 Homebox(Item + Location 双表)」介绍**已不同**——这一版把 Item 与 Location **合并成单张 `entities` 表**(下文详述)。
- **事实 vs 推测约定**:正文中带源码路径/字段名的论断为「事实(代码可证)」;凡是「建议/推断/可能」类表述,均显式标注为**推测**,集中在第三章「建模启示」与第四章。

---

## 一、前言:这份调研为何存在

委托人计划自研一个「三合一」库存系统,把三类彼此割裂的需求统一进**一个数据模型**:

1. **保质期 / best-before + 临期提醒**:食品/药品/耗材的到期日跟踪,且能**提前 N 天**主动提醒。
2. **耐用品台账**:序列号、保修、价值、多级位置层级、照片的资产登记与全生命周期追溯。
3. **耗材库存**:工具/螺丝/线材等大量消耗品的出入库流水、最低库存阈值、低库存告警。

在动手自研前,先评估三个成熟开源方案是否能直接采用或改造。结论是:**没有任何单一现成项目同时满足这三条**——

- **Homebox** 把「耐用品台账(②)」做到开箱即用的强项,却**完全没有**有效期/批次/库存流水/阈值告警的概念(①③ 缺失)。
- **Grocy** 把「食品保质期(①)」与「耗材出入库阈值(③)」做得很专业,却在「耐用品台账(②)」几近空白(序列号/保修/位置层级全无),且**没有任何主动推送**。
- **InvenTree** 在 ①③ 上原生支持、② 也覆盖大半(唯缺保修),是三者中能力最全的,但它是面向**工程制造/电子元件 BOM** 的「重型」系统,对「家庭三合一」属过度设计,且关键的临期提醒**默认关闭、只发订阅者**。

因此:**三个方案各有强项但都不完整,且强项分布在不同象限**,与其在某一个上做大量反向改造、不如**博采三者数据模型之长,自研一套统一模型**。本报告即为该决策的依据,核心产出是对三者**核心数据模型取舍**的对比与提炼。

---

## 二、三份单项目简报全文(内联)

> 以下三节为三个子代理各自产出的完整简报,**原样内联**,互不裁剪。每份均自包含、可独立阅读。

---

## 2.1 Homebox — 单项目简报全文

> 调研对象:`sysadminsmedia/homebox`
> 版本:**v0.26.1**,commit **1b40a9ef**(tag `v0.26.1`)
> 调研方法:以 backend 的 ent ORM schema、数据库迁移、API 路由、后台任务源码为真相,文档仅作辅助。
> 重要提示:本 v0.26.1 的数据模型与网上常见的「旧版 Homebox(Item + Location 双表)」介绍**已经不同**——这一版把 Item 和 Location **合并成了一张 `entities` 表**(下文详述)。

### 1. 定位与概览

- **是什么**:面向「家庭用户」的自托管(self-hosted)家庭物品清单 / 物资管理系统。强调简单、轻量、可移植(单容器、默认 SQLite、内嵌 Web UI,空载内存 < 50MB)。
- **维护方 / 活跃度**:由 SysadminsMedia 组织维护(原作者 @hay-kot)。仍在活跃开发——迁移文件里能看到 2026 年的频繁结构性变更(合并实体、OIDC、API key、密码重置、导出/导入等)。提供官方 Demo、Discord、Weblate 多语言翻译。
- **License**:AGPL-3.0(仓库根 `LICENSE`,GNU Affero GPLv3)。
- **成熟度**:成熟、生产可用的开源项目,但定位明确是「家庭物品台账」,**不是**面向食品保质期或工业耗材消耗的库存系统。
- **核心理念(README)**:Rich Organization(分类/位置/标签/自定义字段)、Powerful Search、Image Upload、Document & Warranty Tracking、Purchase & Maintenance Tracking、响应式 UI。

### 2. 功能清单

- 物品(item)与位置(location)统一为「实体(entity)」管理,支持任意层级的父子嵌套。
- 物品标识:序列号、型号、制造商、资产编号(asset_id,可自增)。
- 保修:终身保修开关、保修到期日、保修详情。
- 购买/出售记录:购买日期/来源/价格、出售日期/对象/价格/备注。
- 数量(quantity,**支持小数**)、是否投保(insured)、归档(archived)。
- 标签(Tag,带颜色/图标,**支持父子嵌套**)。
- **自定义字段**(EntityField:text/number/boolean/time 四种类型)。
- **实体模板**(EntityTemplate:预填默认值,批量创建同类物品)。
- 附件(照片 / 手册 / 保修单 / 收据 / 通用附件,带缩略图生成)。
- **维护记录与计划维护**(MaintenanceEntry:已完成日期 + 计划日期 + 成本)。
- 通知器(Notifier:基于 shoutrrr 的多通道推送,仅用于「今日到期的计划维护」)。
- 多用户 / 多组(Group)/ 组内角色(role)/ 邀请令牌。
- 认证:本地登录 + OIDC、API Key、密码重置。
- 导入 / 导出(CSV 行级导入;整组 ZIP 备份导出与导入,异步任务化)。
- 标签打印(labelmaker)、条码扫描(前端 zxing)、产品搜索(repo_product_search)。

### 3. 技术栈

| 层 | 技术 |
|---|---|
| 后端语言 | Go 1.26 |
| ORM | **ent (entgo.io/ent v0.14.6)** —— schema 即代码,代码生成 |
| HTTP 路由 | go-chi/chi v5 |
| 迁移 | pressly/goose v3(SQL 脚本 + Go 迁移混用) |
| 数据库 | **SQLite3(默认)/ PostgreSQL** |
| 通知 | containrrr/shoutrrr v0.8(支持邮件、Telegram、Discord、Slack、Gotify、ntfy 等多通道 URL) |
| 对象/文件存储 | gocloud.dev blob(本地文件系统或 S3 等) |
| 任务/消息 | gocloud.dev pubsub(默认 `mem://`,可选 Kafka / NATS / RabbitMQ) |
| 可观测性 | OpenTelemetry(otelchi) |
| 前端 | **Nuxt 4 + Vue 3 + Pinia + TailwindCSS**,组件库 reka-ui,@tanstack/vue-table,date-fns,zxing/barcode-detector(扫码) |
| 部署 | 单 Docker 容器(标准 / rootless / hardened 三种镜像),前端打包内嵌进 Go 二进制 |

### 4. 架构与实现方式

- **分层清晰**:`internal/data/ent`(ORM 生成代码与 schema)→ `internal/data/repo`(仓储层,封装查询)→ `internal/core/services`(业务服务)→ `app/api/handlers`(HTTP 控制器)→ `app/api/routes.go`(路由)。
- **schema-first + 代码生成**:ent schema 文件(`backend/internal/data/ent/schema/*.go`)定义字段与边(edges),ent 生成全部 ORM 代码。
- **Mixin 复用**:`BaseMixin`(id UUID + created_at + updated_at)、`DetailsMixin`(name + description)、`GroupMixin`/`UserMixin`(多租户外键)被各实体复用(`schema/mixins/base.go`、`schema/group.go`、`schema/user.go`)。
- **多租户**:一切核心数据挂在 `Group` 下(owned edges,级联删除);User 与 Group 多对多,通过 `user_groups` 连接表携带「每成员角色」。
- **值得注意的取舍 —— 把 Item 与 Location 合并成 Entity(本版本最大设计决定)**:
  - 旧版有独立的 `items` 与 `locations` 两张表;本版本通过迁移 `20260416120001_merge_entities.go` 合并为一张 **`entities`** 表。
  - 用 `EntityType.is_location`(布尔)区分「这是一个位置/容器」还是「这是一个物品」。
  - 「物品放在某位置」和「位置嵌套子位置」**都用同一条自引用边 `parent → children` 表达**。即:位置树、物品归属、容器套容器,全是同一种父子关系。
  - 好处:统一查询、统一附件/字段/维护、容器可以本身也是物品(如「工具箱」既是位置又是有价值的耐用品);坏处:语义靠 `is_location` 标志位区分,模型更「松」,约束更弱。
- **异步任务化**:导出/导入、缩略图生成走 pubsub 订阅;周期任务(清理过期 token、发通知、查 GitHub 新版本)走自定义 recurring runner(`app/api/recurring.go`)。

### 5. 核心数据模型(重中之重)

Schema 源码目录:`backend/internal/data/ent/schema/`

#### 5.1 实体一览

| 实体 | 文件 | 说明 |
|---|---|---|
| **Entity** | `entity.go` | 核心。物品 **和** 位置都是它,靠 EntityType 区分 |
| **EntityType** | `entity_type.go` | 实体的「类型/分类」,带 `is_location` 区分位置 vs 物品 |
| **EntityField** | `entity_field.go` | 实体的自定义字段(键值,4 种类型) |
| **EntityTemplate** | `entity_template.go` | 创建实体时的默认值模板 |
| **TemplateField** | `template_field.go` | 模板里预定义的自定义字段 |
| **Tag** | `tag.go` | 标签(可嵌套父子),与 Entity 多对多 |
| **MaintenanceEntry** | `maintenance_entry.go` | 维护记录(含计划维护日) |
| **Attachment** | `attachment.go` | 附件/照片/文档,挂在 Entity 上 |
| **Group** | `group.go` | 租户/家庭组,一切数据的归属 |
| **User / UserGroup** | `user.go` / `user_group.go` | 用户、用户-组多对多(带角色) |
| **Notifier** | `notifier.go` | 通知器(shoutrrr URL) |
| **Export** | `export.go` | 导出/导入任务记录 |
| 其余 | `api_key.go` `auth_tokens.go` `auth_roles.go` `password_reset_tokens.go` `group_invitation_token.go` | 认证/授权相关 |

#### 5.2 Entity 关键字段(物品/位置统一表)

来自 `entity.go` + mixin:

| 字段 | 类型 | 默认/约束 | 用途 |
|---|---|---|---|
| id | UUID | 主键 | |
| created_at / updated_at | time | 自动 | |
| name | string(≤255) | 必填 | 名称(来自 DetailsMixin) |
| description | string(≤1000) | 可选 | |
| **quantity** | **float** | 默认 1 | **数量,支持小数**(迁移 `20260314103000_item_quantity_decimals`) |
| insured | bool | false | 是否投保 |
| archived | bool | false | 归档 |
| asset_id | int64 | 0 | 资产编号(可自增,见配置 AutoIncrementAssetID) |
| import_ref | string(≤100) | 可选 | 导入引用键 |
| notes | string(≤1000) | 可选 | 备注 |
| sync_child_entity_locations | bool | false | 子实体位置同步开关 |
| **serial_number** | string(≤255) | 可选,有索引 | 序列号 |
| **model_number** | string(≤255) | 可选,有索引 | 型号 |
| **manufacturer** | string(≤255) | 可选,有索引 | 制造商 |
| **lifetime_warranty** | bool | false | 终身保修 |
| **warranty_expires** | time | 可选 | **保修到期日** |
| **warranty_details** | text(≤1000) | 可选 | 保修详情 |
| purchase_date | time | 可选 | 购买日期 |
| purchase_from | string | 可选 | 购买来源 |
| purchase_price | float | 0 | 购买价格 |
| sold_date / sold_to / sold_price / sold_notes | time/string/float/string | | 出售记录 |

**Entity 的边(关系)**:
- `parent ↔ children`(自引用,Unique parent):**唯一的层级关系**——既是位置树,也是物品归属(物品的 parent 是它所在的位置/容器)。
- `entity_type`(必填,指向 EntityType):决定它是位置还是物品。
- `tag`(多对多 Tag)。
- owned:`fields`(EntityField,级联删)、`maintenance_entries`(级联删)、`attachments`(级联删)。

> 注意:**没有单独的「位置」表,没有「数量变动流水/出入库记录」表,没有批次/有效期相关字段**。数量就是 entity 上一个 float 字段,直接覆盖式更新(create/update/patch 时 `SetQuantity`),不留历史。

#### 5.3 EntityType(区分位置/物品的关键)

`entity_type.go`:
- `is_location` bool(默认 false)—— **唯一区分「位置/容器」与「普通物品」的标志**。
- `icon` string。
- 边:`entities`(该类型下的实体)、`default_template`(默认模板)。

#### 5.4 EntityField(自定义字段)

`entity_field.go`:`type` 枚举(text/number/boolean/time)+ `text_value`/`number_value`/`boolean_value`/`time_value`。这是**唯一的扩展点**——任何 schema 没有原生支持的属性(如「保质期」「最低库存」)都只能塞进这里当通用自定义字段,数据库层面无类型语义、无校验、无法触发提醒。

#### 5.5 MaintenanceEntry(维护/计划维护)

`maintenance_entry.go`:`entity_id` + `date`(已完成日)+ `scheduled_date`(计划日)+ `name` + `description` + `cost`。**这是全系统唯一带「计划未来日期 + 会触发通知」的实体**(详见第 7 节)。

#### 5.6 它如何建模 物品/位置/数量/批次/有效期(总结)

- **物品**:Entity(is_location=false 的 EntityType)。
- **位置**:Entity(is_location=true),通过 parent/children 构成位置树;物品的 parent 指向它所在位置。
- **数量**:Entity.quantity,单一 float 字段,**无出入库流水**。
- **批次(batch/lot)**:**完全没有**。
- **有效期 / 保质期(best-before)**:**完全没有**(schema 与全部迁移中检索 `best-before / expir / shelf-life / batch / lot` 均无匹配;唯一的到期字段是 `warranty_expires`,语义是保修而非保质期)。

### 6. 三大需求能力映射

#### 需求① 保质期 / best-before + 临期提醒 —— **缺失**

- **证据**:`entity.go` 中**没有** best-before / expiry / shelf-life 字段;全 schema 与全迁移目录检索这些关键词无任何命中。唯一日期字段是 `warranty_expires`(保修)、`purchase_date`、`sold_date`、维护的 `scheduled_date`。
- 唯一变通:用 EntityField 自定义字段塞一个 time 类型「过期日」——但这只是个不会被任何逻辑读取的裸字段,**不会触发任何提醒**。
- **临期提醒**:系统唯一的提醒机制只针对「计划维护」,且只在「正好是今天」时触发(见第 7 节),**无法做提前 N 天提醒**。
- 结论:**缺失**。即便手工凑字段也无提醒,基本不可用。

#### 需求② 耐用品台账(序列号、保修、价值、位置层级、照片)—— **原生支持(本项目强项)**

- **序列号 / 型号 / 制造商**:`serial_number` / `model_number` / `manufacturer`,均为原生字段且建有索引(`entity.go` L58-66, Indexes L25-33)。
- **保修**:`lifetime_warranty` / `warranty_expires` / `warranty_details` 原生字段(L70-76)。
- **价值**:`purchase_price` / `sold_price`,以及容器层级聚合的 `TotalPrice`(`repo_entities.go`);Group 带 currency。
- **位置层级**:parent/children 自引用边 + `is_location`,支持任意深度位置树(`/entities/tree`、`/entities/{id}/path` API)。
- **照片 / 文档**:Attachment 支持 photo/manual/warranty/receipt 类型 + 缩略图。
- **维护记录**:MaintenanceEntry 记录历次维护与成本。
- 结论:**原生支持**,这是 Homebox 设计的核心场景,做得相当完整。

#### 需求③ 耗材库存(出入库、最低库存阈值、低库存告警)—— **缺失 / 勉强凑合**

- **数量**:有 `quantity`(float,支持小数,可表示 1.5 米线材等)。但它是**直接覆盖式**更新的单值,**没有出入库流水(no transaction/ledger table)**——无法回答「这周消耗了多少」。
- **最低库存阈值(min stock / reorder point)**:schema 中**完全没有**此类字段(检索 `min stock / threshold / reorder / low stock` 无命中)。变通也只能用 EntityField 塞个 number,但无逻辑读取。
- **低库存告警**:**没有**。系统不存在「数量低于阈值时通知」的任何代码路径。
- 结论:**缺失**。能记数量,但缺少耗材库存的核心三件套(流水、阈值、告警),只能当「静态计数」用。

### 7. 提醒 / 通知机制(关键,务必看清宣传与实现差异)

- **唯一触发源**:计划维护(MaintenanceEntry.scheduled_date)。
- **触发条件(代码确证)**:`backend/internal/core/services/service_background.go` 的 `SendNotifiersToday`:对每个 Group 调 `MaintEntry.GetScheduled(group, today)`,SQL 条件是 `scheduled_date == 今天` **且** 该维护尚未完成(`date` 为空)。
- **调度**(`backend/app/api/recurring.go` L59-68):一个每小时跑一次的任务,**仅当本地时间 `hour == 8`(早上 8 点)** 时才真正发送。
- **通道**:shoutrrr 的 Notifier URL(邮件 / Telegram / Discord / Slack / Gotify / ntfy 等),先做 SSRF 校验。
- **能否提前 N 天提醒**:**不能**。`GetScheduled` 精确匹配 `scheduled_date == 今天`,没有任何「提前若干天」的窗口逻辑。
- **保修到期(warranty_expires)会提醒吗**:**不会**。没有任何后台任务扫描 `warranty_expires`,它纯粹是个展示字段。
- 与宣传对照:README 宣传「Maintenance schedules / tracking」属实,但**仅限当天提醒**;**保质期临期提醒、保修到期提醒、低库存告警三者全部不存在**。

### 8. 数据进出与集成

- **CSV 导入**:`POST /entities/import`(`service_entities.go`),列含 `HB.import_ref / HB.location / HB.tags / HB.quantity / HB.name / HB.serial_number / HB.warranty_expires / HB.purchase_* / HB.sold_*` 等(见 `app/api/demo.go` 与 `internal/core/services/reporting/io_row.go`)。**导入列里没有保质期、没有最低库存字段**。
- **CSV 导出**:`GET /entities/export`。
- **整组备份**:Export 实体 + ZIP 归档(`/exports`),异步任务,导出/导入两种 kind,7 天自动清理。
- **REST API**:基于 chi 的 v1 API(`app/api/routes.go`),实体 CRUD、tree、path、duplicate、attachments、maintenance、notifiers、统计等。API Key 认证支持。
- **Webhook**:无传统 webhook;对外推送统一走 shoutrrr Notifier(只在维护通知场景被调用)。

### 9. 多用户 / 部署 / 存储 / 认证

- **多用户 / 多租户**:User ↔ Group 多对多,`user_groups` 连接表带 per-group 角色(迁移 `20260511000000_per_group_role`);数据按 Group 隔离。组邀请令牌、`default_group_id`。
- **认证**:本地用户名密码、**OIDC**(issuer+subject 映射,迁移 `20251123000000_add_oidc_identity`)、**API Key**、密码重置令牌;可配置 `disable_registration` / `allow_local_login`。
- **部署**:单容器,默认 SQLite + 内嵌前端;三种镜像(标准/rootless/hardened)。端口默认 7745。
- **存储**:gocloud.dev blob,默认本地文件系统,可切 S3 等;附件路径相对化(迁移 `20250826`)。
- **数据库**:SQLite3 或 PostgreSQL。
- **消息**:默认内存 pubsub(`mem://`),可换 Kafka/NATS/RabbitMQ。

### 10. 优点

1. **耐用品台账非常完整且开箱即用**:序列号/型号/制造商/保修/购买价/出售/照片/维护记录,字段齐全且有索引。
2. **统一 Entity 模型优雅**:位置与物品同表、容器可嵌套、容器本身也可是有价值的物品——对「家庭/工具间」场景建模自然。这是最值得借鉴的一点。
3. **轻量、易部署**:Go 单二进制 + SQLite + 内嵌前端,资源占用极低。
4. **扩展点齐全**:自定义字段 + 模板 + 标签(可嵌套)+ 自定义实体类型,业务可塑性强。
5. **工程质量高**:schema-first(ent)、清晰分层、多租户、OIDC/API Key、OTel 可观测、异步任务、SSRF 防护,代码注释充分。
6. **通知通道丰富**:借 shoutrrr 一行 URL 接入十余种推送渠道。

### 11. 局限与短板(对照三需求)

1. **保质期 / best-before 完全缺失**(需求①核心)——数据模型里没有任何过期/批次概念。
2. **没有任何「提前提醒」能力**——唯一提醒只在「计划维护当天早 8 点」触发,做不到临期/到期前 N 天预警;保修到期也不提醒。
3. **耗材库存能力薄弱**(需求③)——数量是单值无流水(无出入库台账),无最低库存阈值,无低库存告警。
4. **数量无历史**:quantity 覆盖式更新,无法统计消耗速率、无审计。
5. **语义靠标志位**:位置 vs 物品仅靠 `EntityType.is_location` 区分,约束较松,需要应用层守纪律。
6. **批次/序列分离缺失**:同一物品的多个批次(不同保质期/不同购入日)无法在模型层区分,只能拆成多条 Entity 手工管理。

### 12. 关键源码位置速查表

| 关注点 | 路径(均相对仓库根 `homebox/`) |
|---|---|
| 核心实体 schema(物品+位置) | `backend/internal/data/ent/schema/entity.go` |
| 实体类型(is_location) | `backend/internal/data/ent/schema/entity_type.go` |
| 自定义字段 | `backend/internal/data/ent/schema/entity_field.go` |
| 模板 / 模板字段 | `backend/internal/data/ent/schema/entity_template.go`、`template_field.go` |
| 维护记录(计划维护) | `backend/internal/data/ent/schema/maintenance_entry.go` |
| 附件/照片 | `backend/internal/data/ent/schema/attachment.go` |
| 标签 | `backend/internal/data/ent/schema/tag.go` |
| 通知器 | `backend/internal/data/ent/schema/notifier.go` |
| 组/用户/多租户 mixin | `backend/internal/data/ent/schema/group.go`、`user.go`、`mixins/base.go` |
| 通知发送逻辑(只发今日维护) | `backend/internal/core/services/service_background.go`(`SendNotifiersToday`) |
| 「今日计划维护」查询 | `backend/internal/data/repo/repo_maintenance_entry.go`(`GetScheduled`,L81) |
| 周期任务(8 点发通知 etc.) | `backend/app/api/recurring.go`(L59-68) |
| 实体仓储(数量/层级/聚合) | `backend/internal/data/repo/repo_entities.go` |
| Item↔Location 合并迁移 | `backend/internal/data/migrations/sqlite3/20260416120001_merge_entities.go` |
| 数量改小数迁移 | `backend/internal/data/migrations/sqlite3/20260314103000_item_quantity_decimals.sql` |
| API 路由 | `backend/app/api/routes.go` |
| CSV 导入/导出列定义 | `backend/internal/core/services/reporting/io_row.go`、`backend/app/api/demo.go` |
| 配置项 | `backend/internal/sys/config/conf*.go` |
| 前端 | `frontend/`(Nuxt 4 + Vue 3 + Pinia + Tailwind) |

> **子代理给「三合一」的取舍建议(基于本项目)**:
> - **值得直接借鉴**:Homebox 的「Entity 统一模型 + EntityType.is_location + parent/children 单层级树」是对**耐用品台账 + 多级位置**的优秀范式,可作为统一模型的骨架。
> - **必须自行补齐(本项目没有)**:① 一张**批次/库存批(stock lot)子实体**,携带 `best_before` / `quantity` / `received_at`;② 一张**库存流水(stock movement / ledger)表**记录出入库,替代覆盖式 quantity;③ 物品级 `min_quantity / reorder_point` 字段 + 后台扫描任务;④ 一个**通用的「提前 N 天」提醒引擎**(扫描 best_before、warranty_expires、min_quantity 等多种触发源),而非 Homebox 那种「只发当天维护」的硬编码逻辑。

---

## 2.2 InvenTree — 单项目简报全文

> 单项目调研,仅针对仓库 `InvenTree`。
> 委托背景:自研「三合一」库存系统,核心三需求 —— ① 保质期/best-before + 临期提醒;② 耐用品台账(序列号/保修/价值/位置/照片);③ 耗材库存(出入库/最低阈值/低库存告警)。关注重点:核心数据模型如何取舍。

### 0. 版本说明(重要,先读)

委托方给出的基线是 **0.13.0,commit `457fe16f9`,声称比 0.13.0 晚约 3000 commit**。但实际检出代码自报版本并不止于此:

- `src/backend/InvenTree/InvenTree/version.py` → `INVENTREE_SW_VERSION = '1.4.0 dev'`
- `INVENTREE_API_VERSION = 503`(`InvenTree/api_version.py`)
- HEAD commit `457fe16f9`,提交日期 **2026-06-13**;`git rev-list --count HEAD` = 17894。

**结论**:本仓库实际是 InvenTree **1.4.0-dev** 开发主线(2026 年中),远新于 0.13.0(0.13.0 是 2023 年的版本)。本简报基于这份真实代码撰写。这点对委托方很关键 —— 本简报描述的若干能力(尤其「临期主动提醒 `check_stale_stock` 定时任务」)是 0.13.0 之后才加入的,**如果委托方真用 0.13.0,这些能力可能不存在或形态不同**。所有结论以检出代码为准并标注路径。

### 1. 定位与概览

- **是什么**:InvenTree 是一套开源**库存与零件管理系统(Inventory Management System)**,定位偏**工程/制造/电子元件**场景:强调底层库存管控(stock control)、零件追踪(part tracking)、BOM(物料清单 Bill of Materials)、采购/生产订单。核心是 Python/Django 后端(数据库 + Admin + REST API),前端是独立的 React SPA。
- **维护方/活跃度**:由 InvenTree Developers 社区维护(GitHub `inventree/InvenTree`),活跃度很高(本检出主线提交密集到 2026-06)。有官方 Demo、文档站、Docker 镜像、插件生态。
- **License**:**MIT**(`LICENSE`),非常宽松,可自由借鉴/二次开发/商用。
- **当前版本**:见 §0,检出为 1.4.0-dev / API v503。
- **成熟度**:**高**。多 app 分层、完整 REST API(带 OpenAPI/drf-spectacular)、迁移历史完整、插件体系、后台任务队列、报表/标签打印、条码、多语言。属于「重型」成熟产品,不是玩具项目。

### 2. 功能清单(概要)

- **零件管理(Part)**:分类树、变体(variant)/模板(template)、修订(revision)、参数(parameters)、BOM、供应商/制造商关联、定价缓存。
- **库存管理(Stock)**:StockItem(实际库存)、位置树(StockLocation)、序列号、批次(batch)、过期日期、库存状态、库存调拨/出入库、库存盘点(stocktake)、完整变更历史(StockItemTracking)、测试结果(StockItemTestResult)。
- **采购/销售/退货订单(order app)**:PurchaseOrder / SalesOrder / ReturnOrder。
- **生产(build app)**:BuildOrder、从 BOM 消耗库存产出成品。
- **公司(company app)**:Company(可同时是 customer/supplier/manufacturer)、SupplierPart、ManufacturerPart、价格档。
- **横切能力**:条码/二维码、报表与标签打印、附件、自定义状态、Webhook、通知、数据导入导出、插件系统、多用户/权限/所有权(ownership)。

### 3. 技术栈

**后端**(`src/backend/requirements.txt`,均为锁定版本):
- **Python ≥ 3.11**,**Django 5.2.x**,**Django REST Framework**(REST API)。
- **drf-spectacular**:OpenAPI schema。
- **django-q2**(`django-q2==1.10.0`):后台任务队列 / 定时任务(scheduled tasks),临期检查、低库存通知、定价计算都跑在这上面。
- **django-mptt**(`0.18.0`):树形结构(位置树、分类树、零件变体树)。
- **django-money**:货币/价格字段(`MoneyField`)。
- **django-allauth**(含 mfa/saml/socialaccount/openid):认证、2FA、SSO。
- 其它:django-filter、django-cors-headers、django-maintenance-mode、django-dbbackup、Pillow(图片)、django-cleanup(自动清理孤儿文件)。

**前端**(`src/frontend/`,独立 SPA):
- **React + TypeScript + Vite**,UI 用 **Mantine 9**,国际化用 **Lingui**,图表用 Mantine charts/FullCalendar,代码编辑用 CodeMirror。
- 即新版 "Platform UI (PUI)"。后端通过 REST API 提供数据;另保留 Django Admin。

**部署形态**:
- 官方主推 **Docker / docker-compose**(`contrib/`),`Procfile` 说明进程划分(web + worker)。
- 进程:gunicorn(web)+ django-q cluster(worker,跑后台/定时任务)。数据库默认 PostgreSQL(也支持 MySQL/SQLite),媒体/静态文件本地或 S3。

### 4. 架构与实现方式

- **分层**:典型 Django 多 app 单体。每个领域一个 app(`part`、`stock`、`company`、`order`、`build`、`common`、`importer`、`report`、`plugin`、`users`、`machine`)。每个 app 内部 `models.py` + `serializers.py` + `api.py`(DRF 视图)+ `tasks.py`(后台任务)+ `migrations/`。
- **关键设计模式 / 取舍**:
  - **Mixin 组合模型**:模型不直接堆字段,而是多继承一堆 Mixin(`InvenTreeImageMixin`、`InvenTreeAttachmentMixin`、`InvenTreeBarcodeMixin`、`InvenTreeNotesMixin`、`MetadataMixin`、`InvenTreeTree` 等)。即「照片/附件/条码/备注/元数据/树结构」是可插拔横切能力,而非写死在每个表里 —— **对自研统一模型很有借鉴价值**。
  - **MPTT 树**:位置、分类、零件变体都用 mptt 维护层级(`lft/rght/level/tree_id`),支持高效「含子节点」查询。
  - **Part / StockItem 分离**(本项目最核心的建模决策,见 §5):「零件定义」与「实际库存」是两张表。
  - **通用 Attachment + 通用通知**:`common.Attachment` 用 `model_type + model_id` 泛型关联任意模型;通知通过插件式 NotificationMethod 分发。
  - **后台 offload**:重活(低库存检查、定价、临期扫描)统一 `offload_task()` 丢给 django-q worker,不阻塞请求。
  - **自定义状态可扩展**:库存状态用 `InvenTreeCustomStatusModelField`,允许用户自定义状态码。

### 5. 核心数据模型(重中之重)

#### 5.1 顶层取舍:Part(定义) vs StockItem(实物)

这是 InvenTree 整个建模的灵魂,**自研统一模型必须先理解这层分离**:

- **`Part`(零件 / 物品「定义」,抽象概念)** — `part/models.py:462`
  描述「这是什么东西」:名称、描述、分类、单位、是否可序列化、最低/最高库存阈值、默认过期天数、默认存放位置、图片等。**Part 本身不带数量、不带具体位置、不带序列号**。一个 Part 可对应 0..N 个实物库存。

- **`StockItem`(实际库存,物理实例)** — `stock/models.py:422`
  描述「具体某一份/某一件库存」:指向哪个 Part、数量、所在位置、序列号、批次、过期日期、状态、采购价、所有者等。**数量、位置、序列号、过期日期都在 StockItem 上**。

> 直白说:`Part` = 「螺丝 M3×10 这个品类」;`StockItem` = 「放在 A 柜 3 层的这 500 颗 M3×10、批次 240501、2026-01 入库」。
> 同一台带序列号的设备 = 一个 `StockItem`(quantity=1 + serial)。

#### 5.2 主要实体与关键字段

**Part(`part/models.py:462` 起)**

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` / `description` / `keywords` | Char | 名称/描述/搜索关键词 |
| `category` | FK→PartCategory(树) | 分类(mptt 树) |
| `IPN` | Char | 内部料号 Internal Part Number |
| `revision` / `revision_of` / `variant_of` / `is_template` | Char/FK | 修订与变体体系 |
| `image` | 图片(来自 `InvenTreeImageMixin`) | **单张**主图 |
| `default_location` | FK→StockLocation | 默认存放位置 |
| **`default_expiry`** | PositiveInteger(天) | **该零件默认保质期天数**,新建 StockItem 时据此自动算 `expiry_date`(默认 0=不过期) |
| **`minimum_stock`** | Decimal | **最低库存阈值**(低库存告警依据) |
| `maximum_stock` | Decimal | 最高库存 |
| `units` | Char | 计量单位(pcs/m/kg…) |
| `trackable` | Bool | 是否可追踪唯一件(→ 允许序列号) |
| `assembly`/`component`/`purchaseable`/`salable`/`testable` | Bool | 业务能力开关 |
| `active` / `locked` / `virtual` | Bool | 状态(停用而非删除) |
| 附件 | 来自 `InvenTreeAttachmentMixin` | **多个**附件/照片(走 `common.Attachment`) |

**StockItem(`stock/models.py:422` 起,字段集中在 1065–1272 行)**

| 字段 | 类型 | 说明 |
|---|---|---|
| `part` | FK→Part | 这份库存是什么(必填,虚拟件不可) |
| `location` | TreeFK→StockLocation | **在哪**(可空) |
| **`quantity`** | Decimal(15,5) | **数量**(可小数,支持线材/液体类) |
| **`serial`** + `serial_int` | Char + Int | **序列号**(+整数化副本用于排序/取下一个) |
| **`batch`** | Char | **批次码**(默认由 `generate_batch_code` 生成) |
| **`expiry_date`** | **Date** | **过期/到期日**(可空) |
| `status` | 自定义状态 | OK/受损/隔离/已损毁等(可扩展) |
| `purchase_price` | Money | **单件采购价**(价值台账依据) |
| `packaging` | Char | 包装 |
| `supplier_part` | FK→SupplierPart | 对应供应商料 |
| `belongs_to` / `installed_parts` | 自引用 FK | **安装关系**:此件装在哪件里(整机/部件) |
| `parent` / `children` | 自引用 TreeFK | 库存拆分父子关系 |
| `build` / `consumed_by` / `purchase_order` / `sales_order` | FK | 来源/去向单据 |
| `customer` | FK→Company | 已分配给哪个客户 |
| `owner` | FK→Owner | 所有权(配合 ownership 控制) |
| `stocktake_date` / `stocktake_user` | Date/FK | 最近盘点 |
| `creation_date` | DateTime | 入库/创建时间 |
| `delete_on_deplete` | Bool | 数量归零时是否自动删除 |
| 附件 | `InvenTreeAttachmentMixin` | 该库存的照片/单据(多个) |

约束(`StockItem.clean()`,`stock/models.py:~960-1054`):**有序列号 ⇒ quantity 必须 = 1**;序列号件 `delete_on_deplete=False`。即「序列号 = 唯一个体台账,批次 = 一堆同质量散件」在模型层硬约束区分开。

**StockLocation(`stock/models.py:124`)**
- 基于 `InvenTreeTree`(mptt)的**层级位置树**:仓库→货架→格子任意深度。
- 字段:`name`、`parent`、`structural`(结构性节点,不能直接放货,只能放子位置)、`external`(外部位置)、`location_type`(FK→StockLocationType,可定义类型+图标)、`owner`、`custom_icon`、`pathstring`(全路径串)。
- `get_stock_items(cascade=True)`:可级联统计某位置(含所有子位置)下全部库存。

**其它相关**
- **StockItemTracking**(`stock/models.py:2994`):**完整库存变更历史**,记 `tracking_type`(枚举:创建/移动/数量变化/转为变体/状态变更…)、`deltas`(JSON 记录变化前后)、`user`、`date`、`notes`。删除 StockItem 后历史仍保留(item 置空)。→ **耐用品台账「全生命周期追溯」现成**。
- **StockItemTestResult**(`stock/models.py:3079`):针对可测试件记录测试结果(+附件)。
- **PartCategory**(`part/models.py:69`):分类树,可设默认位置、默认参数模板。
- **Company / SupplierPart / ManufacturerPart**(`company/models.py`):一个 Company 可同时 `is_customer/is_supplier/is_manufacturer`;SupplierPart=某供应商提供的某 Part(SKU、价格档);ManufacturerPart=制造商 + MPN。
- **Attachment**(`common/models.py:1925`):通用附件表,`model_type + model_id` 泛型挂到任意模型,存「照片/文档/数据表」。

#### 5.3 序列号 vs 批次 vs 过期(三需求直接相关)

- **序列号(serial)**:唯一个体,quantity=1。适合**耐用品台账**(每台设备一条 StockItem)。`serial_int` 用于「取下一个序列号」。
- **批次(batch)**:一组同质散件共享一个批次码,quantity 可 >1。适合**耗材**(一批螺丝)与**食品批次**。
- **过期(expiry_date)**:`StockItem.expiry_date`(Date)。新建库存时,若未指定且 `part.default_expiry > 0`,API 会自动算 `今天 + default_expiry 天`(`stock/api.py:1129-1134`)。判定逻辑:
  - `is_expired()`:`expiry_date < 今天` 且仍在库(`stock/models.py:1357`)。
  - `is_stale()`(临期):`expiry_date < 今天 + STOCK_STALE_DAYS` 且在库(`stock/models.py:1332`)。
  - 需先打开全局开关 `STOCK_ENABLE_EXPIRY`(默认 **关**)。

### 6. 三大需求能力映射

#### ① 保质期 / best-before + 临期提醒 —— **可凑合 ~ 原生支持(取决于版本)**

**支持点(代码证据)**:
- `StockItem.expiry_date`(`stock/models.py:1209`)是一等公民字段;`Part.default_expiry`(`part/models.py:1223`)可按品类自动推算到期日;全局开关 `STOCK_ENABLE_EXPIRY`、临期天数 `STOCK_STALE_DAYS`、是否允许卖/用过期货 `STOCK_ALLOW_EXPIRED_SALE/BUILD`(`common/setting/system.py:729-755`)。
- **临期主动提醒(提前 N 天)确实存在**:`part/tasks.py:159 check_stale_stock` 是 `@scheduled_task(DAILY)` **每日定时任务**,扫描 `expiry_date < 今天 + STOCK_STALE_DAYS` 的在库件,按订阅者(subscribers)归并,每人发一封汇总邮件(`notify_stale_stock`,`part/tasks.py:59`),邮件里逐条列「还有几天到期/已过期几天」。→ **这正是委托方要的「提前 N 天提醒」。**

**注意与坑**:
- **这是 InvenTree 偏制造的「批次/批号有效期」语义**,字段叫 `expiry_date`,**不是专门的食品 best-before**,但语义完全可复用(到期日 = best-before)。
- **它对「保质期食品类」并非专门设计**:没有「开封后 N 天」「储存条件」「营养/条形码查商品库」等食品域概念;批次也偏制造批号。把它当通用「到期日」用没问题,当专业食品保质期管理则缺料。
- **强依赖版本**:`check_stale_stock` 这套主动提醒是较新代码(本检出 1.4.0-dev 有)。**0.13.0 很可能只有 `is_expired/is_stale` 标记与列表过滤、没有这个每日推送任务** —— 委托方若锁 0.13.0 必须自己验证。
- 提醒只走「订阅了对应 Part 的用户」;没订阅就收不到。

判定:**到期日建模 + 临期主动提醒在本版本是原生支持;但语义偏制造批次有效期,食品域要自己补。**

#### ② 耐用品台账(序列号/保修/价值/位置/照片)—— **大部分原生支持,唯「保修」缺失**

- **序列号**:✅ 原生。serial + quantity=1 强约束,`trackable` 控制,自动取下一个序列号。
- **价值**:✅ `StockItem.purchase_price`(Money,单件价);Part 侧还有 PartPricing 缓存。
- **位置层级**:✅ StockLocation mptt 树,任意深度,含子位置级联统计。
- **照片**:✅ Part 有单张主图(`InvenTreeImageMixin`);Part 与 StockItem 都支持多附件(`InvenTreeAttachmentMixin`→`common.Attachment`)放照片/单据。
- **全生命周期追溯**:✅ StockItemTracking 记录每次移动/数量/状态变化,删了也留痕 —— 台账审计很强。
- **安装/归属关系**:✅ `belongs_to`/`installed_parts` 表达「此部件装在哪台机器里」。
- **保修(warranty)**:❌ **没有任何 warranty 字段**(全仓库 grep 仅 `order/models.py:2902` 一处文档里把 ReturnOrder 描述为「RMA/warranty 退货」,并非保修期字段)。**保修到期日需自研**,可挂在 `metadata`(JSON) 或自定义字段上,但无原生提醒。

判定:**耐用品台账原生支持度高(序列号/价值/位置/照片/追溯齐全),唯一明显缺口是「保修期 + 保修到期提醒」需自建。**

#### ③ 耗材库存(出入库/最低阈值/低库存告警)—— **原生支持**

- **数量/出入库**:✅ `StockItem.quantity`(Decimal,可小数,线材按米、液体按升都行),配套调拨/增减/拆分/合并 API,每步进 StockItemTracking。
- **最低库存阈值**:✅ `Part.minimum_stock`(`part/models.py:1230`)。
- **低库存告警**:✅ `part/tasks.py:31 notify_low_stock` + `:135 notify_low_stock_if_required`。触发点:`Part` 保存后(`after_save_part`,`part/models.py:~2750`)后台异步检查 `is_part_low_on_stock()`(`总库存 < minimum_stock`),低于阈值就给订阅者发「低库存通知」邮件;还会沿零件树向上对父零件检查。
- **批次**:✅ batch 字段管理同批耗材。

判定:**耗材场景是 InvenTree 的主场,出入库/阈值/告警全原生。**

> ⚠️ 低库存告警的**触发机制要注意**:它由 **Part 的 save 信号**触发(库存数量变化最终会触发 Part 相关保存/重算),并在后台任务里判断;**不是一个每日定时扫描全表**。极端情况下若某种变更路径没触发 Part save,可能漏报 —— 自研时建议改成「事件触发 + 每日兜底扫描」双保险(InvenTree 对临期就是用每日定时任务兜底的)。

### 7. 提醒 / 通知机制

- **触发来源**:
  - 低库存:**事件驱动**(Part 保存后台检查,`is_part_low_on_stock`)。
  - 临期/过期:**每日定时任务** `check_stale_stock`(django-q `@scheduled_task(DAILY)`)。
  - 通用通知:各类业务事件经 `common.notifications.trigger_notification()` 分发。
- **接收对象**:对该 Part **订阅(subscribe)** 的用户(`part.get_subscribers()`,可向上含父零件订阅者)。**非订阅者收不到** —— 这是个重要约束。
- **通道(NotificationMethod,插件式)**(`plugin/builtin/integration/core_notifications.py`):
  - **站内 UI 通知**(`InvenTreeUINotifications`)
  - **Email**(`InvenTreeEmailNotifications`,模板见 `email/low_stock_notification.html`、`email/stale_stock_notification.html`)
  - **Slack**(`InvenTreeSlackNotifications`,webhook)
  - 可通过插件扩展更多通道。
- **能否「提前 N 天」**:✅ 临期可以(`STOCK_STALE_DAYS` 即提前量);低库存是「低于阈值即报」,阈值本身就是提前量。
- 相关全局设置:`STOCK_ENABLE_EXPIRY`(默认关,必须先开)、`STOCK_STALE_DAYS`(默认 0 = 不报临期,必须设 >0)。**两个默认值都意味着开箱即用时临期提醒是关闭的**,需手工配置。

### 8. 数据进出与集成

- **REST API**:全功能 DRF API,带 OpenAPI schema(drf-spectacular),版本号 `INVENTREE_API_VERSION`(本检出 503)。官方另有 Python 客户端库。
- **导入/导出**:独立 `importer` app(`importer/models.py`:`DataImportSession`/`DataImportColumnMap`/`DataImportRow`),支持上传文件→列映射→逐行校验导入;模型多带 `IMPORT_ID_FIELDS`(如 StockItem 用 `serial`,Part 用 `IPN/name`)。导出走 DRF + 导出插件(`plugin/builtin/integration/exporter`)。
- **Webhook**:`common/models.py:1412 WebhookEndpoint` + `WebhookMessage`,带 token 校验,可接收外部 webhook;事件系统(`plugin/base/event`)可对外推送。
- **插件体系**:成熟的 Mixin 式插件(`plugin/base/` 下 action/barcode/event/label/locate/mail/supplier/ui/validation/currency/urls 等 Mixin)。可自定义条码解析、通知通道、供应商对接、导出格式、UI 面板、校验逻辑、序列号转换、批次码生成等。
- **条码**:每个核心模型有 `barcode_model_type_code`(StockLocation=`SL`、ManufacturerPart=`MP`…),内建条码/二维码生成与扫描定位。

### 9. 多用户 / 部署 / 存储 / 认证

- **多用户**:✅ Django 用户/组 + 细粒度权限;额外有**所有权(ownership)**机制(`STOCK_OWNERSHIP_CONTROL`,位置/库存可绑 Owner=用户或组,沿树继承)。
- **认证**:django-allauth,支持本地账号、**2FA/MFA**、**SAML/OpenID/Social SSO**;另有 OAuth toolkit、DRF token / SimpleJWT。
- **部署**:Docker/compose 为主;web(gunicorn)+ worker(django-q cluster)分离;DB 默认 PostgreSQL(支持 MySQL/SQLite)。
- **存储**:媒体文件(图片/附件)本地或对象存储;django-cleanup 自动清理孤儿文件;django-dbbackup 支持备份。
- **前后端分离**:React SPA 走 API;保留 Django Admin 作底层管理。

### 10. 优点(对自研的借鉴价值)

1. **Part / StockItem 分离**是教科书级的库存建模:定义与实物解耦,数量/位置/序列号/到期日只落在实物上。统一模型应直接采纳这层分离。
2. **序列号件 vs 批次件用同一张 StockItem 表 + 约束区分**(serial⇒qty=1),既统一又能覆盖「耐用品逐件」和「耗材成批」——正好对应委托方需求②③合一。
3. **Mixin 横切能力**(图片/附件/条码/备注/元数据/树)可插拔,模型干净,易扩展自定义字段(metadata JSON)。
4. **位置树(mptt)** 通用且高效,含子位置级联查询开箱即用。
5. **完整变更历史 StockItemTracking**:台账审计、追溯天然具备。
6. **临期用每日定时任务兜底、低库存用事件触发**——两种提醒范式都有现成实现可抄。
7. **通知通道插件化**(UI/Email/Slack/可扩展),与触发逻辑解耦。
8. MIT 协议,可放心借鉴代码与模型设计。

### 11. 局限与短板(对照三需求)

1. **无「保修(warranty)」概念**:耐用品需求②里的保修期/保修到期提醒**完全缺失**,需自研字段 + 提醒。
2. **偏制造/电子,不懂食品**:`expiry_date` 是通用到期日而非专业 best-before;无「开封后保质」「储存条件」「商品条码查营养/品牌库」「按食材消耗」等家庭/食品域概念。当作通用到期日可用,当 Grocy 式食品管家则缺一大块。
3. **临期/过期默认关闭**:`STOCK_ENABLE_EXPIRY` 默认 False、`STOCK_STALE_DAYS` 默认 0,开箱不提醒,必须手工配置;且提醒只发给「订阅该 Part 的用户」。
4. **提醒依赖订阅模型**:没有「全局收件人/家庭成员都收到」的简单模式,需要先维护订阅关系。
5. **低库存告警是事件触发**(Part save 后台检查),非每日全表扫描,理论上存在漏触发路径;自研建议加每日兜底。
6. **重型/学习曲线陡**:BOM、build、order、变体、修订、ownership 等对「家庭三合一库存」是过度设计;直接部署会很重,**更适合借鉴数据模型而非整套搬用**。
7. **版本不确定性**(见 §0):委托给的 0.13.0 与实际检出 1.4.0-dev 差距大;临期主动推送等关键能力可能只存在于新版。锁版本前务必核对。
8. **单图主图**:Part 只有一张 `image`(多图要走附件);若要「耐用品多角度照片」需用 Attachment 体系。

### 12. 关键源码位置速查表

| 主题 | 文件:行 |
|---|---|
| 版本号 | `src/backend/InvenTree/InvenTree/version.py:18`(SW)、`InvenTree/api_version.py:4`(API) |
| **StockItem 模型 / 字段** | `src/backend/InvenTree/stock/models.py:422`(类),字段 1065–1272 |
| `expiry_date` 字段 | `stock/models.py:1209` |
| `is_expired` / `is_stale` | `stock/models.py:1357` / `:1332` |
| 序列号校验 / quantity=1 约束 | `stock/models.py:~1004-1028`、`validate_serial_number ~900` |
| 批次码生成 | `stock/generators.py`(`generate_batch_code`) |
| **StockLocation(位置树)** | `stock/models.py:124`;StockLocationType `:60` |
| **StockItemTracking(历史)** | `stock/models.py:2994`;StockItemTestResult `:3079` |
| **Part 模型** | `src/backend/InvenTree/part/models.py:462` |
| `default_expiry` / `minimum_stock` / `maximum_stock` | `part/models.py:1223 / 1230 / 1239` |
| `is_part_low_on_stock` | `part/models.py:2741` |
| 低库存触发(Part save 信号) | `part/models.py:~2746 after_save_part` |
| **低库存通知任务** | `part/tasks.py:31 notify_low_stock` / `:135 notify_low_stock_if_required` |
| **临期每日定时任务** | `part/tasks.py:159 check_stale_stock` / `:59 notify_stale_stock` |
| 库存过期/批次相关全局设置 | `common/setting/system.py:706-789`(`STOCK_ENABLE_EXPIRY:729`、`STOCK_STALE_DAYS:741`) |
| expiry 自动按 default_expiry 计算 | `stock/api.py:1129-1134` |
| 通知通道(UI/Email/Slack) | `plugin/builtin/integration/core_notifications.py:19/63/119` |
| 通知分发核心 | `common/notifications.py`(`trigger_notification`) |
| 公司/供应商/制造商 | `company/models.py`(Company:78 / ManufacturerPart:485 / SupplierPart:600) |
| 通用附件(照片) | `common/models.py:1925 Attachment` |
| Webhook | `common/models.py:1412 WebhookEndpoint` |
| 数据导入 | `importer/models.py:29 DataImportSession` |
| 插件 Mixin 基类 | `plugin/base/`(event/notification/barcode/ui/validation…) |
| 后端依赖 | `src/backend/requirements.txt` |
| 前端依赖 | `src/frontend/package.json`(React/Vite/Mantine/Lingui) |

---

## 2.3 Grocy — 单项目简报全文

> 调研对象:仓库 `grocy`
> 版本:**v4.6.0**(version.json,ReleaseDate 2026-03-06),实际 HEAD 为 commit **c0a9d615**,即 **v4.6.0 之后第 26 个 commit**(`git describe` = `v4.6.0-26-gc0a9d615`)。本简报结论基于该 HEAD 的源码。
> 方法:以数据库迁移 SQL(`migrations/*.sql`,共 256 个)+ 服务层(`services/StockService.php` 等)为真相来源,README/docs 仅作辅助并逐条验证。

### 1. 定位与概览

- **是什么**:Grocy 是一个 **web-based、self-hosted 的「家庭杂货 + 家务管理」系统**,自我定位为 "ERP beyond your fridge"(超越冰箱的 ERP)。核心是**食品库存管理**(买进、消耗、保质期跟踪),外加购物清单、食谱/膳食计划、家务(chores)、任务(tasks)、电池(batteries)、设备(equipment)等家庭管理模块。
- **维护方 / 活跃度**:由个人开发者 **Bernd Bestel** 维护的 hobby project(README 原文:"This is a hobby project")。项目历史长(`Copyright (c) 2017-2026`),迭代持续(256 个迁移文件、v4.x 系列、HEAD 比 v4.6.0 还新 26 个 commit),生态成熟(有官方 demo、Docker 镜像 linuxserver/grocy、桌面版 grocy-desktop、多语言 Transifex 翻译、社区插件)。
- **License**:**MIT**(`LICENSE.md`)。
- **成熟度**:生产级、被广泛自托管的成熟项目。对**食品保质期场景**打磨极深(冷冻/解冻/开封后保质期、按到期日先消耗等),但本质是「围绕食品库存」的设计,其它实体(equipment/batteries)是很薄的附属模块。

### 2. 功能清单

| 模块 | 能力概述 | 与三需求关系 |
|---|---|---|
| **Stock(库存)** | 按**批次(stock entry)**入库,记录 best_before_date / 价格 / 数量 / 位置;消耗(consume)、开封(open)、移库(transfer)、盘点(inventory);批次级别 FIFO/到期优先消耗 | ①③ 核心 |
| **保质期跟踪** | best-before-date,due_type(过期=丢弃 / 仅提示),开封后/冷冻后/解冻后默认保质天数,"due soon" 清单,日历事件 | ① 核心 |
| **最低库存(min stock)** | 每个 product 的 `min_stock_amount`,低于阈值进 "missing products" 列表,可自动加入购物清单 | ③ 核心 |
| **Shopping list(购物清单)** | 多清单,缺货/过期可自动补入 | 辅助 |
| **Quantity units + 换算** | 采购/库存/消耗/计价 四种单位 + 任意单位间换算(含传递闭包推导) | 通用 |
| **Locations(位置)** | 库存位置;批次可指定具体位置;`is_freezer` 冷冻标志 | ②③ 通用 |
| **Product groups / barcodes / pictures** | 分组、多条码、产品图片 | 通用 |
| **Batteries(电池)** | 充电周期跟踪(charge_interval_days),下次充电提醒 | 概述 |
| **Equipment(设备)** | 极薄:仅 name/description + 说明书文件 + userfields | ②(很弱) |
| **Chores / Tasks** | 周期性家务、一次性任务,due 日期 | 概述 |
| **Recipes / Meal plan** | 食谱、膳食计划、按食谱缺料生成购物清单 | 概述(与三需求无关) |
| **Userfields / Userentities** | **自定义字段**和**完全自定义实体**(关键扩展点,见 §6) | ② 凑合方案 |
| **REST API + Calendar(ICS)** | 完整 REST API;库存/任务/家务/电池到期导出为 iCal | §7 §8 |

### 3. 技术栈

- **后端**:**PHP 8.5**(README 要求),框架为 **Slim**(微框架,`routes.php` + controllers)。数据访问用轻量 ORM **LessQL**(代码里 `$this->DB->products()` 风格;多处注释 "LessQL needs an id column")。
- **数据库**:**SQLite**(要求 3.40+)。**几乎全部业务逻辑都用 SQL 表达**:大量 **VIEW**(stock_current、stock_next_use、stock_missing_products、quantity_unit_conversions_resolved 等)和 **TRIGGER**(级联删除、单位换算级联、约束)做计算,服务层主要做事务编排。
- **前端**:服务端渲染 **Blade 模板**(`views/*.blade.php`)+ jQuery/Bootstrap 风格 JS(`public/viewjs/*.js`),前端通过同一套 REST API 取数。条码扫描用 ZXing(纯客户端摄像头)。
- **部署形态**:典型 PHP 应用——webserver 指向 `public/`,`data/` 目录可写(放 SQLite 库、config、上传文件、备份)。官方提供 Docker 镜像与 Windows 桌面版(内嵌 webserver)。无需独立数据库服务器(SQLite 单文件)。

### 4. 架构与实现方式

- **分层**:Routes(`routes.php`)→ Controllers(页面控制器 + `controllers/Api/*` API 控制器)→ Services(`services/*Service.php`,单例 `GetInstance()`)→ DatabaseService(LessQL/PDO)→ SQLite。中间件(`middleware/`)做认证、CORS、错误处理。
- **关键设计取舍 —— 「逻辑下沉到数据库」**:这是 Grocy 最显著的特征。当前库存状态、单位换算、缺货判断、消耗顺序等都是 **SQL VIEW**;数据一致性靠 **TRIGGER**(如 `cascade_product_removal` 删产品时级联清 stock/log/barcodes/conversions;`cascade_change_qu_id_stock` 改库存单位时把所有相关表的数量按换算因子重算)。好处是「单一真相」、前后端共用;代价是逻辑藏在 SQL 里、可移植性绑死 SQLite、调试门槛高。
- **迁移即 schema 史**:无 ORM 自动建表,所有结构变化都是手写编号迁移(`migrations/0001.sql` … `0256`,少数 `.php`)。SQLite 不支持 DROP COLUMN,所以**重命名旧表→建新表→拷数据→删旧表**的模式反复出现(products 被这样重建多次:0103、0155、0207;stock 在 0049 重建)。**这意味着要看「当前」schema,不能只看 0001,必须追到最后一次重建 + 之后所有 ALTER**。
- **缓存视图**:后期引入 `cache__*` 物化表(如 `cache__products_last_purchased`、`cache__quantity_unit_conversions_resolved`)替换昂贵的实时 VIEW 以提性能。

### 5. 核心数据模型(重中之重)

下面给出**当前 HEAD 的有效字段**(已合并历次重建与 ALTER),并标注引入来源。

#### 5.1 `products`(产品 = 主数据/SKU,**不是**库存实体)

每个 product 是一种「商品定义」,**本身不带库存数量**;数量来自 `stock` 表的批次聚合。

| 字段 | 含义 | 引入 |
|---|---|---|
| `id`, `name`(UNIQUE), `description` | 基本 | 0001 |
| `product_group_id` | 分组 | — |
| `active` | 启用 | — |
| `location_id` | **默认**库存位置 | 0001 |
| `shopping_location_id` | 默认购买店铺 | — |
| `qu_id_purchase`, `qu_id_stock`, `qu_id_consume`, `qu_id_price` | 采购/库存/消耗/计价 **四种数量单位** | stock 0001;consume 0210;price 0219 |
| `min_stock_amount` | **最低库存阈值**(③ 低库存告警的核心) | 0001 |
| `default_best_before_days` | 入库默认保质天数(① ) | 0001 |
| `default_best_before_days_after_open` | 开封后保质天数 | 0046 |
| `default_best_before_days_after_freezing` / `_after_thawing` | 冷冻后/解冻后保质天数 | 0097 |
| `due_type`(1/2) | **1=best-before(到期只提示,不算过期丢弃);2=expiration(到期即视为过期/不可用)** | — |
| `picture_file_name` | 产品图片 | — |
| `enable_tare_weight_handling`, `tare_weight` | 称重去皮(液体/散装) | — |
| `parent_product_id` | 父产品(**仅支持 1 层嵌套**,trigger 强制) | — |
| `cumulate_min_stock_amount_of_sub_products` | 子产品最低库存汇总 | — |
| `quick_consume_amount`, `quick_open_amount` | 快速消耗/开封默认量 | consume 0207;open 0214 |
| `hide_on_stock_overview`, `no_own_stock` | 不在库存总览显示 / 自身不持库存(仅作父/虚拟) | — |
| `should_not_be_frozen`, `treat_opened_as_out_of_stock`, `move_on_open`, `default_consume_location_id`, `disable_open` | 冷冻/开封/消耗位置相关行为开关 | 0207、0248 |
| `default_purchase_price_type`, `auto_reprint_stock_label`, `calories` … | 杂项 | 0212、0248 等 |

> 注意:**没有** serial_number / warranty / purchase_price(单价存在批次上)/ asset_value 这类**耐用品台账**字段。

#### 5.2 `stock`(库存批次 —— 「每个批次一行」)★关键★

**这是理解 Grocy 数据模型的核心:`stock` 表是「每入库一批 = 一行」**。一个 product 在库可以对应多行 stock(不同保质期、不同价格、不同位置批次)。当前库存数量 = 对该 product 的所有 stock 行 `SUM(amount)`(由 `stock_current` 视图聚合,`migrations/0233.sql`)。

| 字段 | 含义 | 引入 |
|---|---|---|
| `id` | 自增主键(单行) | 0004 |
| `product_id` | 所属产品 | 0004 |
| `amount` DECIMAL(15,2) | 该批次当前剩余量(库存单位) | 0049 改为小数 |
| `best_before_date` DATE | **该批次的保质期/到期日**(① 真正落地处) | 0004 |
| `purchased_date` DATE | 购买日 | 0004 |
| `stock_id` TEXT | **批次逻辑 ID**(同一次入库的多个 unit 共享,用于 group/可视化;非自增 id) | 0004 |
| `price` DECIMAL(15,2) | 单价(按 1 库存单位) | 0049 |
| `open` (0/1), `opened_date` | 是否已开封 + 开封时间 | 0049 |
| `location_id` | **该批次所在具体位置**(可覆盖产品默认位置,支持「同一产品分布在多个位置/冷冻柜」) | 0051 |
| `shopping_location_id` | 购买店铺 | 0099 |
| `note` | 批次备注 | 0123 |

- **`stock_log`**:与 `stock` 几乎同构,外加 `transaction_type`、`used_date`、`spoiled`、`undone`/`undone_timestamp`(支持撤销)。是**完整的出入库流水/审计日志**——所有 purchase/consume/open/transfer/inventory 都写一条 log,可撤销。(0005、0049)
- **消耗顺序**:由视图 **`stock_next_use`** 决定(`migrations/0241.sql` 等),默认规则注释明确写着:**「默认消耗位置优先 → 已开封优先 → 到期日最早优先(first-due) → 最早购买优先(FIFO)」**,即 `ORDER BY (default_consume_location) , open DESC, best_before_date ASC, purchased_date ASC`。这是「先吃快过期的」的落地。

#### 5.3 `locations`(位置)

| 字段 | 含义 | 引入 |
|---|---|---|
| `id`, `name`(UNIQUE), `description` | 基本 | 0002 |
| `is_freezer` | 是否冷冻位置(影响保质期重算:存入触发 after_freezing,取出触发 after_thawing) | 0097 |
| `active` | 启用 | 0218 |

> **没有位置层级(parent_location_id)**。位置是**扁平**的单层列表。需求②要的「位置层级(房间→柜子→抽屉)」原生不支持。

#### 5.4 `quantity_units` + `quantity_unit_conversions`(数量单位与换算)★设计亮点★

- `quantity_units`:`id / name / name_plural / description / active`。
- `quantity_unit_conversions`(`migrations/0082.sql`):`from_qu_id / to_qu_id / factor / product_id(可空)`。`product_id` 为空 = **全局默认换算**;非空 = **该产品专属换算**。
- 视图 **`quantity_unit_conversions_resolved`**(0207 重写为递归 CTE)做**传递闭包**:能从「产品专属 > 全局默认 > 库存单位 1:1」按优先级,**推导任意两单位间的换算因子**(例如 箱→袋→片)。这是 Grocy 数据模型里最精巧的部分,把「同一商品多套单位(买一箱、存按瓶、用按毫升)」做得很彻底。对需求③(线材按米/卷/盘换算)有借鉴价值。

#### 5.5 关系总览

```
quantity_units ──< quantity_unit_conversions >── products (qu_id_purchase/stock/consume/price)
locations ──(默认)── products ──< stock (批次,每行一批,带 best_before_date/price/location_id/open)
                                   └──< stock_log (流水/审计/可撤销)
product_groups ── products ──< product_barcodes (多条码,带 amount/qu/price)
products ── parent_product_id ── products (仅 1 层)
shopping_list >── products
```

聚合视图:`stock_current`(产品当前量/价值/最早到期)、`stock_current_location_content`(按位置)、`stock_missing_products`(低于 min_stock)、`stock_next_use`(消耗顺序)。

### 6. 三大需求能力映射

#### ① 保质期 / best-before + 临期提醒 —— **原生支持(强项)**

- **建模**:`stock.best_before_date`(每批次独立到期日,§5.2);产品级默认保质天数 + 开封/冷冻/解冻后天数(§5.1);`due_type` 区分「best-before 仅提示」vs「expiration 到期作废」。
- **临期判断**:`StockService::GetDueProducts(int $days = 5)`(`services/StockService.php:727`)直接 `WHERE best_before_date <= date('now', '$days days')` —— **支持「提前 N 天」**。N 由用户设置 `stock_due_soon_days`(默认 5,`config-dist.php:192`)控制。`GetExpiredProducts()` 用 `best_before_date < date() AND due_type = 2`。
- **"x = 2999-12-31 永不过期"** 约定(README:`x` 展开为 2999-12-31)用于不会过期的物品——对需求②③ 里「无保质期」的物品也适用。
- 结论:**① 是 Grocy 的看家本领,直接可用,且批次级精度 + 开封/冷冻语义比一般系统细。**

#### ② 耐用品台账(序列号、保修、价值、位置层级、照片) —— **凑合 / 多处缺失**

逐项核对(证据为 schema):
- **序列号**:**缺失**。products/stock 无 serial 字段。`stock_id` 是批次组 ID 不是序列号。
- **保修(warranty)**:**缺失**。无 warranty/purchase_date-for-warranty 字段。
- **价值**:**部分**——`stock.price` 是单价、`stock_current.value` 是库存总价值,但这是「商品价值」语义,非「资产折旧/购置价」台账。
- **位置层级**:**缺失层级**——`locations` 扁平无 parent(§5.3)。
- **照片**:**支持**——`products.picture_file_name`;且有通用 `FilesService` + equipment 的说明书文件。
- **Equipment 模块**:`migrations/0041.sql` 显示 equipment 只有 `name/description/instruction_manual_file_name` 三个有效字段,控制器仅额外挂 userfields(`controllers/EquipmentController.php:37`)。**它不进 stock、不带序列号/保修/价值,基本是「设备说明书登记本」,不能当耐用品台账用。**
- **唯一可行路线 = Userfields + Userentities**:`migrations/0066.sql` 的 `userfields/userfield_values` 允许给任意实体(含 products/equipment)加**自定义字段**(text/number/date/checkbox 等);`migrations/0085.sql` 的 `userentities/userobjects` 允许**自建全新实体**并配 userfields。理论上可手工搭出「序列号/保修到期/购置价」字段。但:**这是凑合方案**——没有内置 UI/校验/到期提醒针对这些自定义字段(userfields 不进 due/calendar 逻辑),位置层级仍无解。
- 结论:**② 凑合**,且要靠 userfields/userentities 自己拼,序列号、保修到期提醒、位置层级三项原生都没有。

#### ③ 耗材库存(出入库、最低库存阈值、低库存告警) —— **原生支持(可直接用)**

- **出入库**:`stock` + `stock_log` 完整支持 purchase/consume/inventory/transfer,带流水与撤销(§5.2)。
- **最低库存阈值**:`products.min_stock_amount`(§5.1)。
- **低库存告警**:视图 `stock_missing_products`(`migrations/0215.sql`)= `min_stock_amount - 在库量 > 0` 的产品集合;`StockService::GetMissingProducts()`(:744)读它;可由 `AddMissingProductsToShoppingList()`(:21)**自动补入购物清单**。
- **单位换算**适合耗材(按盘买、按米用),见 §5.4。
- **短板**:Grocy 面向「食品」,无「螺丝按规格 M3/M4 的属性化分类」「批号/合规」等工业耗材特性,但这些可用 userfields 凑;核心的出入库+阈值+告警是**齐全的**。注意 Grocy 的「告警」是**被动清单**(见 §7),不是主动推送。
- 结论:**③ 原生支持**,是除①外第二强的场景。

| 需求 | 结论 | 关键证据 |
|---|---|---|
| ① 保质期 + 临期提醒(提前 N 天) | **原生支持(强)** | `stock.best_before_date`;`GetDueProducts($days)` StockService:727;`stock_due_soon_days` 默认 5 |
| ② 耐用品台账(序列号/保修/价值/位置层级/照片) | **凑合**(序列号✗、保修✗、位置层级✗;价值/照片部分;靠 userfields/userentities 拼) | equipment 0041 仅 3 字段;locations 无 parent;userfields 0066 / userentities 0085 |
| ③ 耗材库存(出入库/阈值/告警) | **原生支持** | `min_stock_amount` 0001;`stock_missing_products` 0215;`AddMissingProductsToShoppingList` StockService:21 |

### 7. 提醒 / 通知机制 —— **没有主动推送,只有「到期清单 + 拉取式日历」**

这是必须纠正的常见误解:**Grocy 不会主动给你发提醒(无邮件 / 无 push / 无 webhook 通知)。**

- **触发与计算**:到期/缺货完全是**查询时即时计算**——`best_before_date <= date('now','+N days')`(临期)、`< date()`(过期)、`min_stock_amount - 在库 > 0`(缺货)。N 来自用户设置 `stock_due_soon_days`(默认 5)。
- **呈现通道(全是被动)**:
  1. **UI 列表**:stock overview 把临期/过期/缺货项高亮(`public/viewjs/stockoverview.js` 调 `stock/volatile?due_soon_days=N`;`stockentries.js:193` 用 `stock_due_soon_days` 算 dueThreshold)。要看到「提醒」你得**主动打开页面**。
  2. **日历(ICS / iCal 订阅)**:`CalendarService::GetEvents()`(`services/CalendarService.php`)把库存到期日、任务/家务/电池到期、膳食计划生成为日历事件,导出为 iCal feed。这是**拉取式**——你把这个 feed 订阅进 Google Calendar / 手机日历,由**外部日历 App** 负责弹提醒。Grocy 自身不发通知。
- **唯一的 webhook**:`helpers/WebhookRunner.php` + 各 API 控制器里的 webhook 调用,经核实**全部是 `GROCY_LABEL_PRINTER_WEBHOOK`(标签打印机)**,与到期/库存通知无关。全仓 grep 无 SMTP / mail() / push / pushover / gotify 的真实实现。
- 结论:**「提前 N 天提醒」在数据/查询层面成立(可配 N 天),但「提醒」的投递必须靠外部(订阅 ICS 让日历 App 弹,或自己轮询 API)。** 自研三合一系统若要主动推送,需在 Grocy 之上自建(这恰是可借鉴其 due 计算 SQL、但要补通知层的点)。

### 8. 数据进出与集成

- **REST API**:**完整且覆盖全功能**(README:web 前端就是用这套 API)。`grocy.openapi.json`(OpenAPI 规范,86 个 summary 端点)+ 内置 Swagger UI(`/api`)。含**通用对象 API** `/objects/{entity}`(对 products/locations/quantity_units/shopping_list/equipment/batteries 等做 CRUD),以及 stock 专用动作(add/consume/open/transfer/inventory)、`stock/volatile`(临期/过期/缺货)等。**用 Grocy 当后端、自己做前端/集成完全可行。**
- **认证**:**API key**(`migrations/0028.sql` 等的 api_keys 表;`ApiKeyService`),请求头携带。
- **导入导出**:数据库即单个 SQLite 文件,`update.sh` 会把整个安装打包成 `.tgz` 备份(含 data 目录)。无内置 CSV 批量导入 UI(社区另有工具),但可经 API 批量灌数据。
- **第三方集成**:条码外部查询插件机制(`plugins/`,默认 Open Food Facts 插件);标签打印机 webhook;ICS 日历订阅;Home Assistant / 各种社区 add-on(README 指向 grocy.info/addons)。

### 9. 多用户 / 部署 / 存储 / 认证

- **多用户**:支持(users 表 0020/0026,sessions 0022);有**权限/角色**机制(后期迁移引入 user permissions);**大量设置是 per-user**(`DefaultUserSetting`,如 `stock_due_soon_days`、日历颜色),但库存数据本身是**单一共享库存**(非按用户隔离的多租户)。
- **认证**:内置用户名/密码(默认 admin/admin),session;API key 用于程序访问;支持反代做 header/LDAP 等(社区/配置层)。
- **部署**:PHP + SQLite,webserver 指向 `public/`,`data/` 可写。官方 Docker(linuxserver/grocy)、桌面版。**单文件 SQLite**——轻量、易备份,但并发写能力有限(家庭规模够用,大规模/高并发不适合)。
- **存储**:SQLite 数据库 + `data/` 下上传文件(产品图片、设备说明书等)+ 备份。

### 10. 优点

1. **保质期/批次模型成熟到位**:`stock` 每批次一行 + best_before_date + 开封/冷冻/解冻语义 + 「先消耗最快过期」消耗顺序,是教科书级的临期库存建模,需求① 几乎零改造可用。
2. **单位换算系统精巧**:四类单位(采购/库存/消耗/计价)+ 产品专属/全局换算 + 递归传递闭包,处理「买一箱、存按瓶、用按毫升」非常彻底,对耗材多单位有借鉴价值。
3. **完整 REST API + 通用对象 API**:整套功能可编程化,适合当后端被三合一系统包裹。
4. **完整审计流水 + 可撤销**:`stock_log` 记录所有事务并支持 undo。
5. **MIT 许可、成熟稳定、自托管、SQLite 零运维**。
6. **Userfields / Userentities 扩展点**:能在不改代码的情况下加自定义字段/实体。

### 11. 局限与短板(对照三需求)

1. **无主动通知**(§7):只有到期清单 + 拉取式 ICS,自研系统要的「提前 N 天主动推送」需自建通知层。**这是与「宣传期望」最容易落差的点**。
2. **耐用品台账几乎缺位**(②):序列号 ✗、保修/保修到期提醒 ✗、资产购置价/折旧 ✗;equipment 模块极薄(仅 name/描述/说明书);**位置层级 ✗(locations 扁平,无 parent_location_id)**。只能用 userfields/userentities 硬凑,且这些自定义字段不进 due/日历逻辑。
3. **「食品中心」设计偏置**:数据模型默认每个 stock 批次是「同质可累加的量」,不适合「每台设备一个唯一实体带独立序列号/保修」的台账语义(那是 unit-level 而非 batch-level 模型)。
4. **绑死 SQLite + 逻辑藏在 SQL VIEW/TRIGGER**:可移植性差,迁移到 Postgres/MySQL 需重写大量视图触发器;调试/二次开发门槛较高。
5. **嵌套仅 1 层**(parent_product_id 触发器强制),BOM/复杂组合品建模受限。
6. **单一共享库存**,非多租户隔离;SQLite 并发写有限。

### 12. 关键源码位置速查表

| 主题 | 文件 / 路径 |
|---|---|
| products 初始定义 | `migrations/0001.sql` |
| products 最近一次完整重建(看当前字段基线) | `migrations/0207.sql`(其后 ALTER:0210 qu_consume、0212、0214 quick_open、0219 qu_price、0248 disable_open 等) |
| stock / stock_log / shopping_list 重建(批次结构、小数量、价格、开封) | `migrations/0049.sql` |
| stock 批次加 location_id | `migrations/0051.sql`;加 shopping_location_id `0099`;加 note `0123` |
| best_before 开封/冷冻/解冻天数、locations.is_freezer | `migrations/0046.sql`、`migrations/0097.sql` |
| locations(扁平,无层级) | `migrations/0002.sql`(+ active `0218`) |
| quantity_units / conversions 表 + resolved 视图 | `migrations/0082.sql`;递归闭包重写 `migrations/0207.sql` |
| stock_current(当前库存聚合视图,最新) | `migrations/0233.sql` |
| stock_missing_products(低库存判定,最新) | `migrations/0215.sql` |
| stock_next_use(消耗顺序:位置→开封→最早到期→FIFO) | `migrations/0241.sql`(搜 `CREATE VIEW stock_next_use`) |
| product_barcodes(多条码) | `migrations/0103.sql` |
| equipment 表(②,极薄) | `migrations/0041.sql`;控制器 `controllers/EquipmentController.php` |
| batteries 表(充电周期) | `migrations/0013.sql` |
| userfields / userfield_values(自定义字段) | `migrations/0066.sql`(object_id 改 TEXT `0178`) |
| userentities / userobjects(自定义实体) | `migrations/0085.sql` |
| 临期/过期/缺货查询逻辑(提前 N 天) | `services/StockService.php`:`GetDueProducts`(727)、`GetExpiredProducts`(739)、`GetMissingProducts`(744)、`AddMissingProductsToShoppingList`(21) |
| 消耗/开封/移库事务编排 | `services/StockService.php`:`ConsumeProduct`(365)、`OpenProduct`(981)、`GetProductStockEntries`(868)→ `stock_next_use` |
| 日历/ICS 事件(拉取式提醒) | `services/CalendarService.php` |
| 标签打印 webhook(非通知) | `helpers/WebhookRunner.php` + `controllers/Api/StockApiController.php` 等 |
| API 规范 / Swagger | `grocy.openapi.json`、`/api`;通用对象 API `/objects/{entity}` |
| 默认设置(stock_due_soon_days=5 等) | `config-dist.php`(:192) |
| 版本 / 许可 | `version.json`(4.6.0)、`LICENSE.md`(MIT) |

*Grocy 简报基于 commit c0a9d615(v4.6.0 + 26 commits)源码核实;所有能力结论均有对应 migration / service 证据。*

---

## 三、跨项目综合

> 本章为编排者基于上述三份简报的综合分析。凡涉及对「自研统一模型」的建议,均为**推测/提炼**,不替委托方设计架构、不涉及新项目技术栈选型。

### 3.1 能力对比矩阵(三项目 × 三需求)

标注口径:**原生支持** = 数据模型有一等公民字段 + 有对应业务逻辑;**可凑合** = 靠自定义字段/变通能拼出,但无内置语义/校验/提醒;**缺失** = 模型与逻辑均无。

| 需求 | Homebox (v0.26.1) | InvenTree (1.4.0-dev) | Grocy (v4.6.0) |
|---|---|---|---|
| **① 保质期 / best-before + 临期提醒(提前 N 天)** | ❌ **缺失**<br>无任何 expiry/批次字段;唯一提醒是「计划维护当天早 8 点」,做不到提前 N 天 | ✅ **原生支持**(本版)<br>`StockItem.expiry_date` + `Part.default_expiry`;每日 `check_stale_stock` 任务可提前 `STOCK_STALE_DAYS` 天推送。⚠️**默认关闭**、只发订阅者;**0.13.0 可能无此任务** | ✅ **原生支持(最强)**<br>批次级 `best_before_date` + 开封/冷冻/解冻后天数;`GetDueProducts($days)` 可配提前 N 天。⚠️**只有清单/ICS,无主动推送** |
| **② 耐用品台账(序列号/保修/价值/位置层级/照片)** | ✅ **原生支持(最强)**<br>serial/model/manufacturer + 保修三字段 + 购入/售出价 + parent 位置树 + 多类型附件 + 维护记录,全齐 | 🟡 **大部原生,缺保修**<br>serial(qty=1 约束)/purchase_price/mptt 位置树/附件/`StockItemTracking` 全程追溯都有;**唯 warranty 字段完全缺失**,需自建 | 🟠 **可凑合(最弱)**<br>序列号✗、保修✗、位置层级✗(locations 扁平);价值/照片部分有;equipment 模块极薄,只能靠 userfields/userentities 硬拼 |
| **③ 耗材库存(出入库/最低阈值/低库存告警)** | ❌ **缺失**<br>quantity 是单值 float、覆盖式更新无流水;无 min stock 字段;无低库存告警 | ✅ **原生支持(主场)**<br>Decimal 数量 + 调拨/拆合 API + 每步 `StockItemTracking`;`Part.minimum_stock` + `notify_low_stock`。⚠告警靠 Part save 事件触发,非每日扫描 | ✅ **原生支持**<br>`stock`+`stock_log` 完整出入库可撤销;`min_stock_amount` + `stock_missing_products` 视图 + 自动补购物清单 |
| **总体象限** | 纯**资产台账**(②)强;①③ 空白 | **三需求覆盖最全**(①③强、②缺保修),但**重型/过度设计**,altitude 偏工业 | **消耗品/食品**(①③)强;②空白;且**无主动推送** |

一句话:**没有任何单一项目同时满足三需求**——能力最全的是 InvenTree(代价是过度设计 + 缺保修 + 食品域不专),①最专业的是 Grocy(代价是无台账 + 无推送),②最顺手的是 Homebox(代价是 ①③ 全空)。三者强项恰好**互补但不重叠**,这正是自研立项的根据。

### 3.2 数据模型对比(物品定义 / 数量 / 批次 / 有效期 / 位置)

| 维度 | Homebox | InvenTree | Grocy |
|---|---|---|---|
| **核心抽象** | **「万物皆 Entity」**:物品、位置、容器同一张 `entities` 表,靠 `EntityType.is_location` 区分 | **「定义 / 实物分离」**:`Part`(品类定义)vs `StockItem`(物理实例),两张表 | **「批次为中心」**:`products`(主数据)/ `stock`(每批次一行)/ `stock_log`(流水) |
| **物品定义** | 无独立定义层——每个 Entity 既是定义又是实例(扁平) | `Part`:名称/分类/单位/`minimum_stock`/`default_expiry`/默认位置,**不带量/位置/序列号** | `products`:名称/单位×4/`min_stock_amount`/默认保质天数,**本身不带量** |
| **数量** | `Entity.quantity`(单 float,**覆盖式,无历史**) | `StockItem.quantity`(Decimal 15,5),每次变动写 `StockItemTracking` | 不存在 products 上;= 该 product 所有 `stock` 行 `SUM(amount)`,由视图聚合 |
| **批次 (batch/lot)** | ❌ **无概念**(同物多批只能拆多条 Entity 手工管) | ✅ `StockItem.batch`(字符串批次码);一条 StockItem 可代表一批同质散件 | ✅ **批次即 `stock` 一行**(`stock_id` 把同次入库分组);天然多批次 |
| **唯一个体 / 序列号** | ✅ `serial_number` 字段(但与「批次量」无区分机制) | ✅ **serial + 硬约束 `serial ⇒ quantity=1`**——同一张表里用约束区分「逐件」与「成批」 | ❌ 无序列号字段 |
| **有效期 / best-before** | ❌ 无(唯一日期是 `warranty_expires`,语义=保修) | ✅ `StockItem.expiry_date`(实例级);`Part.default_expiry` 自动推算 | ✅ `stock.best_before_date`(**批次级**)+ 开封/冷冻/解冻后天数 + `due_type` |
| **位置** | ✅ **自引用 parent/children 树**(与物品归属共用同一条边),任意深度 | ✅ **mptt 树**(`StockLocation`),任意深度 + 级联统计 + structural 节点 | ❌ **扁平**(`locations` 无 parent),仅 `is_freezer` 标志 |
| **出入库流水** | ❌ 无 | ✅ `StockItemTracking`(类型化 + JSON deltas,删 item 仍留痕) | ✅ `stock_log`(类型化 + **可撤销 undo**) |
| **横切能力实现** | ent Mixin(Base/Details/Group)+ EntityField 自定义字段 | **多继承 Mixin**(Image/Attachment/Barcode/Notes/Metadata/Tree)可插拔 | 字段直接堆在表上 + Userfields/Userentities 扩展 |
| **业务逻辑位置** | Go 服务层 | Python 服务层 + django-q 任务 | **下沉 SQL**(VIEW 算状态、TRIGGER 保一致)——绑死 SQLite |

**三种核心抽象的本质差异**:
- **Homebox = 单层 + 标志位**:最简单、对「容器既是位置又是物品」很自然,但**没有「定义 vs 实例」「散件 vs 逐件」「批次」「有效期」这些库存语义层次**,扩展全靠通用自定义字段。
- **InvenTree = 二层 + 约束**:`Part/StockItem` 分离是脊梁;`serial⇒qty=1` 用**一张表 + 一个约束**同时容纳了「逐件耐用品(②)」与「成批耗材(③)」——这是三者里对「三合一」最贴合的结构。
- **Grocy = 批次行 + 聚合视图**:把「批次」做成一等公民(stock 一行=一批),有效期/位置/价格都落在批次上,数量由聚合得出;对①③极顺,但**缺少 unit-level(逐件)维度与位置层级**,做②先天不足。

### 3.3 建模启示(对「三合一统一模型」的可借鉴点与要避开的坑)

> 以下均为**观察与提炼**(推测性建议),不构成架构设计。

**值得借鉴的范式:**

1. **「定义 / 实例」分离是脊梁**(来自 InvenTree `Part/StockItem`,Grocy `products/stock` 亦同构)。把「这是什么品类」(名称、单位、阈值、默认保质天数、默认位置)与「具体这一份/一件」(数量、位置、序列号、批次、到期日)拆成两层,是同时容纳②③的前提。**反例**:Homebox 把两者压成一层 Entity,导致「同物多批/多到期日」只能拆成多条手工管——这是要避开的坑。

2. **「逐件 vs 成批」用同一张实例表 + 约束统一**(来自 InvenTree `serial ⇒ quantity=1`)。耐用品(②,逐件、带序列号/保修)与耗材(③,成批、带数量)不必拆成两套模型,可用一张「库存实例」表 + 「有序列号则数量必为 1」的约束统一表达。这恰好直击「三合一」的核心张力。

3. **有效期与库存属性属于「实例 / 批次」,不属于「定义」**(InvenTree 放 StockItem、Grocy 放 stock 行,一致)。`best_before` / 到期日 / 价格 / 所在位置都应落在实例层;定义层只放「默认保质天数」用于入库时自动推算(InvenTree `default_expiry`、Grocy `default_best_before_days`)。**批次级**到期(Grocy)比物品级更精确,是值得抄的精度。

4. **数量必须走「流水/账本」而非覆盖**(InvenTree `StockItemTracking`、Grocy 可撤销 `stock_log`)。出入库记成一条条事务,当前量由累加/聚合得出,才能回答「消耗速率」「审计」「撤销」。**反例**:Homebox 的覆盖式单值 float 无历史——避开。

5. **位置用自引用树**(Homebox parent/children、InvenTree mptt 都可)。房间→柜子→抽屉是②的硬需求。**反例**:Grocy 的扁平 `locations` 是其做不了耐用品台账的主因之一——避开扁平位置。Homebox 还示范了「容器本身也可以是一个有价值的物品」(工具箱既是位置又是资产),这个洞察值得保留。

6. **提醒应是一个独立、统一、可配「提前 N 天」的引擎,走可插拔通道**。三者给了完整的正反样本:
   - InvenTree 的**好范式**:临期用**每日定时任务**全表扫描兜底、低库存用**事件触发**,两者都把「触发」与「通道(UI/Email/Slack 插件化)」解耦,且 `STOCK_STALE_DAYS` 即提前量。
   - 要避开的坑:InvenTree 提醒**默认关闭**(`STOCK_ENABLE_EXPIRY`/`STOCK_STALE_DAYS` 默认值导致开箱不提醒)+ **只发订阅者**;Grocy **根本没有主动推送**(只有到期清单 + 拉取式 ICS);Homebox 提醒**硬编码**为「只发当天计划维护、早 8 点」,既不能提前、也不覆盖保修/保质/低库存。
   - 提炼:统一模型应让**一个提醒引擎同时扫描多种触发源**(best_before、warranty_expires、min_quantity……),支持每人/每物可配的提前天数,**事件触发 + 每日兜底双保险**,默认开启,默认通知到责任人。

7. **横切能力做成可插拔 Mixin / 通用附件,而非每表硬堆字段**(InvenTree 的 Image/Attachment/Barcode/Notes/Metadata/Tree Mixin + `model_type+model_id` 通用 Attachment 最干净)。照片、附件、条码、备注、自定义字段对三类物品都需要,抽成横切层可避免模型膨胀。

8. **保修(warranty)是三者共同短板,必须自建**。Homebox 有字段但**不提醒**;InvenTree **连字段都没有**;Grocy 也无。统一模型需把「保修到期日」做成一等公民,并纳入上面第 6 条的提醒引擎(与 best_before 同机制)。

9. **多单位换算**(Grocy 的采购/库存/消耗/计价四单位 + 递归传递闭包)对耗材「按盘买、按米用」很有价值,但也可能对家庭场景**过度**;**推测**:可作为可选能力,而非核心模型必备。

**要避开的坑(汇总):**

- ❌ 把「定义」和「实例」压成一层(Homebox)→ 同物多批/多到期管不了。
- ❌ 覆盖式数量、无流水(Homebox)→ 无消耗统计、无审计、无撤销。
- ❌ 扁平位置(Grocy)→ 做不了耐用品的层级定位。
- ❌ 提醒硬编码/默认关闭/只发订阅者/无推送(三者各有其一)→ 「提前 N 天主动提醒」落空。
- ❌ 业务逻辑下沉 SQL VIEW/TRIGGER 绑死单一数据库(Grocy)→ 可移植性与可维护性差;**推测**统一模型宜把逻辑放应用层。
- ❌ 整套搬用 InvenTree → BOM/build/order/变体/ownership 对家庭三合一是过度设计,**宜借鉴数据模型而非整体移植**。

### 3.4 一句话总评(每项目「最值得抄的一点」+「最大软肋」)

- **Homebox**
  - 最值得抄:**「统一 Entity + `is_location` 标志 + parent/children 单棵树」**——容器即物品、位置即父节点,对耐用品台账 + 多级位置的建模简洁自然。
  - 最大软肋:**完全没有有效期 / 批次 / 库存流水 / 阈值告警**,且提醒硬编码为「只发当天维护」,①③ 几乎为零。

- **InvenTree**
  - 最值得抄:**`Part / StockItem` 定义-实例分离 + `serial ⇒ quantity=1` 约束**——用一张实例表统一「逐件耐用品」与「成批耗材」,外加 Mixin 横切能力与「每日扫描 + 事件触发」双提醒范式。
  - 最大软肋:**无保修概念、对食品域不专、整体重型过度设计**,且临期提醒默认关闭 + 只发订阅者。

- **Grocy**
  - 最值得抄:**批次级 `best_before_date` 模型 + 「位置→开封→最早到期→FIFO」消耗顺序 + 单位换算闭包 + 可撤销流水**——食品/耗材临期管理的教科书。
  - 最大软肋:**无主动推送(只到期清单 + 拉取式 ICS)、耐用品台账缺位(序列号/保修/位置层级全无),且逻辑绑死 SQLite 的 VIEW/TRIGGER**。

---

## 四、推测与不确定性声明

- **事实(代码可证)**:本报告所有带源码路径、字段名、迁移编号、函数行号的论断,均由子代理在对应仓库源码中核验,属可追溯事实。
- **推测/提炼**:第三章 3.3「建模启示」、3.4 中的「最值得抄/最大软肋」判断,以及任何「建议/宜/可」字样的内容,是基于事实的**编排者提炼**,非代码直接断言。
- **版本不确定性(重要)**:
  - **InvenTree** 实际检出为 **1.4.0-dev**(2026 年),而非委托标称的 0.13.0(2023 年)。本报告对 InvenTree ① 的「原生支持」结论**依赖 1.4.0-dev 才有的 `check_stale_stock` 每日任务**;若实际部署锁 0.13.0,临期主动提醒很可能不存在或形态不同,需自行核对 `part/tasks.py` 与 `common/setting/system.py`。
  - Homebox、Grocy 的检出版本与标称一致(分别 v0.26.1、v4.6.0+26),结论可信度高。
- **范围声明**:本调研只评估这三个现成方案对三需求的匹配与其数据模型取舍,**不包含**对自研新项目的架构设计、技术栈选型或实现方案——那是立项之后的工作。

---

*调研日期 2026-06-13 · 编排者综合 · 三份单项目简报由独立子代理基于各自仓库源码产出 · 中间产物保留于 `research/Homebox.md`、`research/InvenTree.md`、`research/Grocy.md`*
