# M5 — 横切能力 + 条码 + 数据导出

> 🌐 **语言:** [English](./M5.md) · 中文(当前)

> **里程碑设计文档 — 自包含。** 与 `docs/plan/roadmap.md`（地图，尤其 §5 M5 + 红线 **§2.8 横切能力做成通用/可复用、不在每张表里重复**——照片/附件走通用 `model_type + model_id`、标签、备注、自定义字段、条码——以及 §2.9 Decimal/Date、§2.10 单一 context/仓储层、§2.11 逻辑在 app 层不在 DB、§2.12 LLM/识别后端是*预留抽象*）一起读；"为什么存在"见 `docs/inspiration/investigation.md`。本文是 *M5 做什么、怎么验收* 的唯一真相源；别再从 roadmap 反推范围。进度只在 roadmap §4 表里追踪。
>
> 内建约定：原子步骤（§9）、盲审检查点（§10）、🟢 部署自测点（§11），手动与编排两种执行都能挂在本文上。
>
> **范围说明（先读）。** roadmap 的 M5 条目列了八项能力。M5 规划中作者**裁剪并重塑**了这份清单（理由见下 + §2）：**备份/恢复延后**（自托管用户暂时手动复制单个 `DATA_DIR` 目录），**CSV 仅导出**（导入延到 LLM 时代——自由格式的列映射正是它的拿手好戏）。最终交付**六项横切能力 + 条码 + 数据导出**，分**四相**（A 通用元数据，B 条码+搜索，C 导出，D 前端）共**13 个原子步骤**，每步独立可部署。

---

## 1. 目标与非目标

**目标（roadmap 的 M5 承诺——日常易用性 + 数据可携带）：** 把光秃秃的数据模型变成一个像样的自托管应用。具体：

- **通用附件/照片** —— 上传图片与文档（小票、保修 PDF），通过**一套**多态机制（roadmap §2.8）挂到任意 item definition、stock instance 或 location 上。文件落在 `DATA_DIR` 下的文件系统里，**内容寻址**（sha256），相同上传自动去重；**引用计数**，删除属主绝不留下孤儿文件。
- **标签 Tags** —— 扁平、可着色的标签，可贴到多种实体（definition/instance/location），表达 category 树表达不了的横切分组。
- **备注 Notes** —— 同样可贴到这几类实体的自由文本备注。
- **自定义字段** —— 每实体的用户自定义键值对（definition 与 instance 上的一个 JSON map），覆盖内置 schema 没有的属性（电压、年份、剂量……）。可作文本搜索。
- **全局搜索** —— 一个 `GET /search` 跨 item definition、stock instance（序列号/条码/自定义字段）、location、category、tag，走 app 层 LIKE（可移植，roadmap §2.11），并置于 **provider 缝**之后，日后可接 LLM 语义搜索。
- **条码扫描 + 商品查询** —— 浏览器摄像头**客户端**解码 1D（EAN/UPC）与 2D（QR）；码值打到 `GET /barcodes/lookup`，经 **`ProductLookupProvider` 缝**解析。M5 只实装**内部** provider（扫到已绑定的码 → 直接跳到该 definition，复购秒入库）；外部商品库/LLM provider 是预留钩子（§2.12，M9）。
- **CSV / JSON 导出** —— 导出 item definition、stock instance、location，用于报表、保险清单或离线留档。

**完成判据（🟢，§11 展开）：** 给物品贴张照片并在画廊里看到，删除后底层文件随之消失（引用计数）；建标签、加备注并挂上；加自定义字段，并能通过全局搜索按自定义值找到物品；给 definition 绑一个条码，再**扫**它跳到该物品并预填入库；跑一次全局搜索，结果按类型分组返回；导出 CSV 并用表格软件打开。CI 保持绿，含 no-drift 契约门；迁移 `0001`–`0027` 在全新库上干净应用。

**非目标（明确不在 M5——延后、重新定范围、或属后续里程碑）：**
- **不做备份/恢复 UI**（从 M5 重新划出——roadmap §5 M5 原把它列为"core"）。自托管运维者通过复制 bind-mount 的 `DATA_DIR`（同时含 SQLite 文件**与** `media/`）来备份。一等公民式的应用内备份/恢复流是后续优化（§12）。
- **不做 CSV 导入**（延后）。导出交付；导入——连同列映射、外键按名解析、校验、dry-run、部分失败处理——延后到与 **LLM 辅助格式化**后端（§2.12，M9）一起做，它恰好适合啃凌乱的表格（§12）。
- **不做外部商品查询。** M5 建好 `ProductLookupProvider` 缝，只实装**内部** provider。Open Food Facts / 其他公开商品库 / LLM 视觉是 M9（§2.12）。日后加一个就是新增一个 provider 类 + 一个设置开关，扫码流程与 UI **完全不动**。
- **不做 LLM 语义搜索。** M5 交付一个 LIKE 的 `SearchProvider`；语义 provider 是预留缝（M9）。
- **不做服务端缩略图。** M5 校验图片并把过大原图降采样封顶；画廊用（封顶后的）原图配懒加载渲染。专门的缩略图流水线是优化（§12）。
- **不做逐请求媒体鉴权 / 内容净化加固。** 媒体经一个以不可猜内容哈希为键的静态挂载分发（capability URL）。逐请求鉴权、上传内容净化、SVG/活动内容处理是 **M6** 加固轮（roadmap §5 M6，呼应 M4 把 SSRF 推到 M6）——M5 只做 §4.2 的基础安全默认。
- **不做可嵌套标签。** roadmap §3 提过"Tag (nestable)"；M5 交付**扁平**标签（已有的 Category 树覆盖层级）。一个经敲定的有意简化（§2）。
- **不做资产标签生成 / 标签打印。** 内部资产标签 + QR/标签生成留在 parking lot；M5 只交付*扫描*侧。`barcodes.symbology` 字段为日后的 `internal` 标签种类留了余地。

---

## 2. 锁定决策（M5 规划中敲定；理由见 roadmap §2/§3 + 规划讨论）

| 领域 | 决策 |
|---|---|
| **一套通用横切机制，而非每表加列** | 附件、标签、备注都**多态**：一个 `(model_type, model_id)` 对引用属主（roadmap §2.8）。允许的属主类型是一个集中校验的小集合——`item_definition`、`stock_instance`、`location`。`model_id` 上**没有 DB 外键**（无法指向多张表）；存在性与**删除级联在服务层**强制（§4.3）。 |
| **附件：文件系统 + 内容寻址 + 引用计数** | 字节落在磁盘 `DATA_DIR/media/<sha256[:2]>/<sha256>`（分片）。一条 `media_files` 行存哈希 + 大小 + 校验后的 content-type + 图片尺寸；一条 `attachments` 行是属主到某 `media_files` 行的一个**引用**（带原文件名 + 可选标题）。相同上传**去重**（同哈希 ⇒ 一份物理文件，N 个引用）。删除最后一个引用即删 `media_files` 行**与**物理文件——**无孤儿**（作者明确要求）。 |
| **图片校验 + 大小封顶；不做缩略图** | 上传时用 **Pillow** 验证确为可解码图片并记录尺寸；超大图在哈希/存储前**降采样**到配置的最大边（默认 2048px），抑制存储膨胀。非图片附件（PDF 等）按大小上限原样存。M5 **不**做服务端缩略图变体（§12）。 |
| **媒体经静态挂载分发，哈希为键（capability URL）** | 内容哈希路径实质不可猜，故 M5 用一个 `StaticFiles` 挂载在 `/media` 分发媒体（高效：range 请求、缓存）。该挂载在 `create_app` 中注册在 SPA catch-all 路由**之前**（否则 catch-all `@app.get("/{full_path:path}")` 会吞掉 `/media/*`）。逐请求鉴权是 **M6**（§1 非目标）。 |
| **标签：扁平、多实体** | 一张 `tags` 表（name 唯一、可选 color）+ 多态 `tag_links` 表。**扁平**（无树——Category 树覆盖层级）。name 唯一性在服务层按大小写不敏感强制（DB 在 `name` 上唯一）。 |
| **自定义字段：JSON 键值 map，可搜索** | `item_definitions` 与 `stock_instances` 上各一个可空 `custom_fields` **JSON** 列。值是 `str → (str\|int\|float\|bool\|null)` 的扁平 map（M5 不嵌套，以便可搜索可渲染）。它被**纳入全局搜索**做序列化文本的子串匹配——所以值*能*被找到。JSON KV 相比结构化字段 schema 有意放弃的是：**带类型/范围查询**、**键一致性分面**、**逐字段类型化 UI**（用低复杂度换的经敲定取舍——§12）。 |
| **条码：独立表、一码对一 definition、provider 缝** | 一张 `barcodes` 表（`definition_id` FK、`code` **全局唯一**、`symbology`、可选 label）。一个 definition 可带**多**码；一个码解析到**一个** definition（确定性查询）。扫描是**客户端**（浏览器内 ZXing，1D + 2D）；解码值打到 `GET /barcodes/lookup?code=`，跑 **`ProductLookupProvider`** 链。M5 只实装 **`InternalProvider`**。 |
| **全局搜索：app 层 LIKE，provider 缝** | 一个 `SearchService` 跨实体仓储跑大小写不敏感子串查询（复用各自已有的 `q` 过滤），结果**按类型分组**且每类型封顶。**不用 SQLite FTS**（保持数据库可换——§2.11）。一个 `SearchProvider` 协议罩在前面，日后可加 LLM **语义** provider 而不动端点/UI。 |
| **CSV 仅导出（也含 JSON）；导入延后** | `GET /export/{entity}?format=csv\|json` 经服务层流式导出 `item_definitions` / `stock_instances` / `locations`（stdlib `csv`；无新模型）。外键按 **id 与 name 双列**导出（如 `category_id` + `category_name`）便于人读。**导入不做**（§1 非目标）。 |
| **备份/恢复延后** | M5 不做。单个 bind-mount 的 `DATA_DIR`（SQLite 文件 + `media/`）**就是**备份单元；运维者复制它。（§1 非目标，§12。） |
| **新依赖（foundations 触动）** | 后端：**`python-multipart`**（multipart 上传）+ **`pillow`**（图片校验/降采样）。前端：**`@zxing/browser`**（+ `@zxing/library`）做浏览器内 1D/2D 解码。属栈追加 ⇒ `AGENTS.md`"Tech stack & commands"后端 + 前端依赖行加上它们（随 Step 1 / Step 11 一起）。Pillow 走 manylinux wheel——JPEG/PNG/WebP **无需额外 apt 包**。 |
| **`DATA_DIR` 变成真正的设置** | `.env.example` 文档化了 `DATA_DIR` 但 `config.py` 没实现。Step 1 加一个 `data_dir` 设置（默认 `./data`），从中派生 `media/`，启动时创建目录，保持现有 `database_url` 行为不变。 |
| **错误码（新增）** | 新增：`attachment.not_found`（404）、`attachment.file_too_large`（413）、`attachment.unsupported_type`（415）、`tag.not_found`（404）、`tag.duplicate_name`（409）、`note.not_found`（404）、`barcode.not_found`（404）、`barcode.duplicate`（409——码已绑定到某 definition）。搜索/导出校验走 Pydantic ⇒ 既有 `validation.invalid_input`。故 M5 加 **八**个 `ErrorCode`，全部经 M1.5 统一错误信封。 |

> 这些扩展 M0–M4 的基础。新表/列、每个新 schema、新端点都过既有 no-drift 契约门（§6）与 M1.5 统一错误信封。新后端/前端依赖 + `/media` 静态挂载 + `DATA_DIR` 设置是那处"foundations"触动（AGENTS.md 依赖行 + 一个 `data_dir` 配置字段）。

---

## 3. 数据模型

六张新表 + 两个增量 JSON 列。全部用 M0 约定：共享 `Base` 上的 SQLAlchemy 2.0 typed `Mapped[...]`、规则在服务层、DB 访问**只**经仓储。两个列增量是可空纯增（不重建表）。

### 3.1 `media_files` — 新表（迁移 `0020`）

物理文件登记表（内容寻址）。每段唯一字节内容一行。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `sha256` | String(64) | 否 | **唯一**（`uq_media_files_sha256`）。内容哈希；也是存储键。 |
| `byte_size` | Integer | 否 | 磁盘大小（图片为降采样后）。 |
| `content_type` | String(128) | 否 | **我方**校验过的 MIME（绝不原样用用户头）。 |
| `width` | Integer | 是 | 图片像素宽（Pillow）；非图片为 NULL。 |
| `height` | Integer | 是 | 图片像素高；非图片为 NULL。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

磁盘路径：`DATA_DIR/media/<sha256[:2]>/<sha256>`（无扩展名；类型在 DB 里）。

### 3.2 `attachments` — 新表（迁移 `0021`）

属主实体到某 `media_files` 行的一个**引用**。多态、计引用的 join。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `media_file_id` | FK→media_files.id | 否 | `ondelete=RESTRICT`（DB 级安全：被引用时 `media_files` 行不能删；服务层只在**最后一个**引用移除后才删行）。 |
| `model_type` | String(32) | 否 | `item_definition` / `stock_instance` / `location`（集中校验，§4.3）。 |
| `model_id` | Integer | 否 | 属主 id（无硬 FK——多态）。 |
| `original_filename` | String(255) | 是 | 显示/下载用。 |
| `title` | String(255) | 是 | 可选标题。 |
| `sort_order` | Integer | 否 | `default 0`；属主内排序。 |
| `uploaded_by` | FK→users.id | 是 | `ondelete=SET NULL`。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

索引：`(model_type, model_id)`（列某属主的附件）；`(media_file_id)`（计引用）。**引用计数** = `COUNT(attachments WHERE media_file_id = x)`——无单独计数列（join 本身*就是*计数）。

### 3.3 `tags` + `tag_links` — 新表（迁移 `0022`、`0023`）

`tags`：

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `name` | String(64) | 否 | **唯一**（`uq_tags_name`）。大小写不敏感唯一性在服务层强制。 |
| `color` | String(32) | 是 | 可选（Mantine 颜色名或 hex）。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

`tag_links`（多态）：

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `tag_id` | FK→tags.id | 否 | `ondelete=CASCADE`（删标签连带删其 link）。 |
| `model_type` | String(32) | 否 | 允许的属主类型。 |
| `model_id` | Integer | 否 | 属主 id（多态）。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

唯一 `(tag_id, model_type, model_id)`（`uq_tag_links_tag_owner`——同一东西不能重复打标）；索引 `(model_type, model_id)`。

### 3.4 `notes` — 新表（迁移 `0024`）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `model_type` | String(32) | 否 | 允许的属主类型。 |
| `model_id` | Integer | 否 | 属主 id（多态）。 |
| `body` | Text | 否 | 自由文本。 |
| `created_by` | FK→users.id | 是 | `ondelete=SET NULL`。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |
| `updated_at` | DateTime(tz) | 否 | `server_default=now()`，更新时刷新。 |

索引 `(model_type, model_id)`。

### 3.5 `item_definitions.custom_fields` + `stock_instances.custom_fields`（迁移 `0025`、`0026`）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `custom_fields` | Text | 是 | JSON 对象字符串；扁平 map `str → (str\|int\|float\|bool\|null)`。NULL = 无。由服务/schema 层经 Pydantic (反)序列化 + 校验；**不用** DB JSON 函数（可移植，§2.11）。 |

> 存为 `Text`（非 DB 原生 JSON 类型）以保持可移植。map 在 app 层校验：键非空 + 限长、值仅标量、合理的字段数上限（默认 50）。

### 3.6 `barcodes` — 新表（迁移 `0027`）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `definition_id` | FK→item_definitions.id | 否 | `ondelete=CASCADE`（definition 的码随它走）。 |
| `code` | String(128) | 否 | **唯一**（`uq_barcodes_code`）——一码解析到一个 definition。 |
| `symbology` | String(16) | 否 | `default 'unknown'`；如 `ean13` / `upca` / `qr` / `code128` / `internal` / `unknown`。app 校验；无 DB CHECK（§2.11）。 |
| `label` | String(255) | 是 | 可选（如"单件" vs "24 件箱"）。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

索引 `(definition_id)`。

### 3.7 迁移清单

| Rev | Step | 做什么 |
|---|---|---|
| `0020` | 1 | 建 `media_files`。 |
| `0021` | 1 | 建 `attachments`。 |
| `0022` | 2 | 建 `tags`。 |
| `0023` | 2 | 建 `tag_links`。 |
| `0024` | 3 | 建 `notes`。 |
| `0025` | 4 | `item_definitions`：加 `custom_fields`。 |
| `0026` | 4 | `stock_instances`：加 `custom_fields`。 |
| `0027` | 5 | 建 `barcodes`。 |

全部可逆，用 `op.create_table` / `op.drop_table`，两个增量列用 SQLite batch-mode `add_column` / `drop_column`（M0 约定）。无数据回填（新表为空；新列默认 NULL）。

---

## 4. 后端设计

### 4.1 分层（扩展 M2/M3/M4）

- **仓储**（`app/repositories/`）：`MediaFileRepository`（按哈希取、建、删、计某文件引用数）、`AttachmentRepository`（建、列属主、取、删、按 media-file 列）、`TagRepository`（CRUD、按名取-ci、列）、`TagLinkRepository`（绑、解、列属主、按标签列属主、存在性）、`NoteRepository`（CRUD、列属主）、`BarcodeRepository`（建、按码取、列某 definition、删）。都延续既有 `q` 过滤列表模式；仓储外**无裸查询**。
- **服务**（`app/services/`）：
  - `MediaStorage`（`app/services/media_storage.py`）—— 唯一碰文件系统的东西：`store(bytes, declared_type) -> MediaFile`（校验 → 降采样 → 哈希 → 不存在则写 → upsert 行）、`path_for(media_file)`、`delete_physical(media_file)`。纯 I/O + Pillow；不掺 HTTP。
  - `AttachmentService` —— `upload(model_type, model_id, file) -> Attachment`（确认属主存在 → `MediaStorage.store` → 建引用）、`list_for(owner)`、`delete(attachment_id)`（删引用 → 若引用计数归 0，删 `media_files` 行 + 物理文件）、`delete_for_owner(model_type, model_id)`（级联辅助）。
  - `TagService` —— 标签 CRUD（大小写不敏感重名守卫 → `tag.duplicate_name`）、`attach`/`detach`(`model_type,model_id`)、`list_for_owner`、`set_tags_for_owner`（整组替换）、`detach_all_for_owner`（级联辅助）。
  - `NoteService` —— 按属主作用域的备注 CRUD；`delete_for_owner`（级联辅助）。
  - `BarcodeService` —— 给 definition 绑码（全局唯一守卫 → `barcode.duplicate`）、解绑、列某 definition；**`lookup(code)`** 委托 provider 链。
  - `ProductLookupService` / providers（`app/services/product_lookup/`）—— 一个 `ProductLookupProvider` Protocol（`lookup(code) -> ProductLookupResult | None`）+ `InternalProvider`（经 `BarcodeRepository.get_by_code` → 绑定的 definition 解析）。服务遍历已配置的 provider 列表，返回首个命中。
  - `SearchService` + `SearchProvider`（`app/services/search/`）—— 一个 `SearchProvider` Protocol（`search(q, types, limit) -> SearchResults`）+ `LikeSearchProvider`（查各实体仓储，§4.5）。服务是日后语义 provider 的缝。
  - `ExportService` —— `export(entity, format) -> Iterator[str]`（流式 CSV/JSON 行），经既有服务/仓储读取。
  - 一个共享的**属主解析**辅助（`app/services/owners.py`）把 `model_type → repository.get` 映射起来，让附件/标签/备注校验属主存在、让级联钩子知道清什么。
- **通用级联**：既有 `ItemDefinitionService.delete`、`StockInstanceService.delete`、`LocationService.delete` 各自在删行**之前**为其 `(model_type, id)` 调 `AttachmentService.delete_for_owner`、`TagService.detach_all_for_owner`、`NoteService.delete_for_owner`（同事务；物理文件删除是提交后 best-effort 步骤，§4.2）。这是多态的代价（§2.8）：DB 无法级联 `model_id`。

### 4.2 媒体存储、校验与分发（最易错的 I/O）

- **上传**（`POST /attachments`，multipart）：路由读 `UploadFile`，服务对总字节封顶（默认 25 MB → `attachment.file_too_large`/413），嗅探类型。**图片**：用 Pillow 打开（`Image.open` + `.verify()`）；不可解码则拒（`attachment.unsupported_type`/415）；记录 `width`/`height`；若任一边 > `media.max_image_edge`（默认 2048）则重新编码降采样（保比例 + EXIF 方向）。**非图片**：content-type 白名单（PDF、纯文本、常见文档）——其余 → `attachment.unsupported_type`。**SVG 拒绝**（活动内容）。我们持久化并日后分发的是校验后的 `content_type`（绝非原始客户端头）。
- **内容寻址 + 去重**：对（可能已重编码的）字节哈希；若已有该 `sha256` 的 `media_files` 行，复用（不二次写）；否则在分片路径写文件并插行。写是**原子的**（临时文件 + rename）。
- **引用计数删除**：移除一个附件只删引用行；服务随后查 `MediaFileRepository.count_references`，归零时删 `media_files` 行并 unlink 物理文件。物理 unlink 与降采样写发生在 DB 事务**之外**（best-effort；unlink 失败记日志，绝不让请求崩——残留文件无害且可扫除）。
- **分发**：在 `DATA_DIR/media` 上的一个 `/media` `StaticFiles` 挂载，**注册在 SPA catch-all 之前**。基础安全默认：以存储的 `content_type` 分发、`X-Content-Type-Options: nosniff`、非图片类型用 `Content-Disposition: attachment`。逐请求鉴权 + 更深净化 = M6（§1 非目标）。挂载以媒体目录存在为条件（启动时从 `data_dir` 创建）。

### 4.3 多态属主与级联

一个 `OWNER_TYPES = {"item_definition", "stock_instance", "location"}` 注册表（一处）喂给：schema 校验（`model_type` 必须在集合内 → 否则 `validation.invalid_input`）、属主存在性检查（`resolve_owner(model_type, model_id)` → 缺失则属主对应的 not-found 码 404）、以及三个 `delete` 服务用的级联映射。日后加新属主类型（如 `category`）是注册表一行的改动。

### 4.4 条码查询与 provider 缝

```
GET /barcodes/lookup?code=<scanned>
  → ProductLookupService.lookup(code):
      for provider in providers:            # M5: [InternalProvider]
          result = provider.lookup(code)
          if result is not None:
              return result
      return None
```

`ProductLookupResult` 带 `source`（`"internal"`）、可选的命中 `definition_id`（+ 轻量 definition 摘要）、以及可选的 `draft`（name/brand/category 提示）用于尚未入库的情况。M5 的 `InternalProvider` 只会返回 `definition_id` 命中。端点返回 `{ found, source, definition, draft }`。**日后** provider（Open Food Facts、LLM 视觉——M9）实现同一 Protocol 并追加进列表，置于设置开关后；扫码流程与前端不变。

### 4.5 全局搜索（LIKE）与 provider 缝

`LikeSearchProvider.search(q, types, limit)` 对每个请求的类型（默认全部）跑：
- **item_definition**：name/description LIKE（复用既有 `q` 过滤）**加** `custom_fields` 文本 LIKE。
- **stock_instance**：serial / model_number / manufacturer LIKE，**加**对 `barcodes.code` 的 join，**加** `custom_fields` 文本 LIKE。
- **location** / **category**：name LIKE（复用既有 `q` 过滤）。
- **tag**：name LIKE。

每类型独立封顶（默认 20），结果为 `{ type: [summaries], …, totals }`。全部大小写不敏感 `func.lower(...).contains(...)`（既有可移植模式）。`SearchProvider` Protocol 是 LLM 语义 provider（M9）的预留缝——`SearchService` 只是遍历/合并 provider。

### 4.6 导出

`ExportService.export(entity, format)` 为 `entity ∈ {item_definitions, stock_instances, locations}` 流式输出行：
- **CSV** 经 stdlib `csv` 写入生成器（表头行 + 数据行），外键扁平为 **id + 解析名**列（`category_id`,`category_name`,`location_id`,`location_name`,`definition_id`,`definition_name`），`custom_fields` 作 JSON 字符串列，tags 作逗号连接的 `tags` 列。`Decimal`/日期用稳定字符串格式（无 locale）。
- **JSON** 作同样扁平记录的流式数组。

经 `StreamingResponse` 分发，带 `Content-Disposition: attachment; filename="<entity>-<date>.csv"`。需认证（session）。经仓储/服务层只读（§2.11）。

### 4.7 API 表面（增量；都在 `settings.api_prefix` 下，默认 `/api`）

| 方法 + 路径 | 认证 | 用途 |
|---|---|---|
| `POST /attachments` | session | Multipart 上传（`model_type`、`model_id`、`file`、可选 `title`）。 |
| `GET /attachments?model_type=&model_id=` | session | 列某属主的附件（带媒体元数据 + 媒体 URL）。 |
| `PATCH /attachments/{id}` | session | 改 `title` / `sort_order`。 |
| `DELETE /attachments/{id}` | session | 移除一个引用（引用计数清理）。404 `attachment.not_found`。 |
| `GET /tags` · `POST /tags` · `PATCH /tags/{id}` · `DELETE /tags/{id}` | session | 标签 CRUD。409 `tag.duplicate_name`，404 `tag.not_found`。 |
| `GET /tags/links?model_type=&model_id=` | session | 某属主上的标签。 |
| `PUT /tags/links` | session | 整组替换某属主的标签（`{model_type, model_id, tag_ids[]}`）。 |
| `GET /notes?model_type=&model_id=` | session | 某属主上的备注。 |
| `POST /notes` · `PATCH /notes/{id}` · `DELETE /notes/{id}` | session | 备注 CRUD。404 `note.not_found`。 |
| `GET /definitions/{id}/barcodes` · `POST /definitions/{id}/barcodes` · `DELETE /barcodes/{id}` | session | 某 definition 的条码。409 `barcode.duplicate`，404 `barcode.not_found`。 |
| `GET /barcodes/lookup?code=` | session | 解析一个扫到的码（provider 链）。 |
| `GET /search?q=&types=&limit=` | session | 全局搜索，按类型分组。 |
| `GET /export/{entity}?format=csv\|json` | session | 流式导出（`entity ∈ item_definitions\|stock_instances\|locations`）。 |
| `PATCH /definitions/{id}` · `POST /definitions` | session | 现也接收/返回 **`custom_fields`**。 |
| `PATCH /instances/{id}` · `POST /instances` | session | 现也接收/返回 **`custom_fields`**。 |

> `model_type` 按属主注册表校验（§4.3）；坏值 → `validation.invalid_input`，缺失属主 → 该属主的 not-found 码。

### 4.8 Schema（`app/schemas/`）

- `AttachmentResponse` —— `id, model_type, model_id, media: {sha256, content_type, byte_size, width, height, url}, original_filename, title, sort_order, created_at`。（`url` = `/media/<ab>/<sha256>`。）`AttachmentUpdate` —— `title?, sort_order?`。
- `TagResponse` / `TagCreate` / `TagUpdate`（`name`、`color?`）；`TagLinkResponse`；`TagSetRequest`（`model_type, model_id, tag_ids[]`）。
- `NoteResponse` / `NoteCreate`（`model_type, model_id, body`）/ `NoteUpdate`（`body`）。
- `BarcodeResponse` / `BarcodeCreate`（`code`、`symbology?`、`label?`）；`BarcodeLookupResponse`（`found, source, definition?, draft?`）。
- `SearchResponse` —— `{ item_definitions: [...], stock_instances: [...], locations: [...], categories: [...], tags: [...], totals: {...} }`，每类型轻量摘要。
- `Export` —— 无响应 schema（流式文件）；路径以二进制/文件媒体类型文档化。
- `CustomFieldsModel` —— 一个校验过的 `dict[str, str|int|float|bool|None]`（键/值/数量上限），并入 Definition 与 Instance 的 create/update/response schema。

---

## 5. 质量门与易错逻辑（Definition of Done）

继承 M0–M4 §5（`make check` 全绿；build；`make codegen` no-drift）。M5 中**必须有单测**的逻辑：

**后端**
- **媒体存储与引用计数**（§4.2——必测）：相同字节传两次 ⇒ **一**条 `media_files` 行 + **两**个 attachment（去重）；删一个 attachment 文件还在；删**最后一个** → 删 `media_files` 行**与**物理文件（无孤儿）；删**属主**级联其全部附件/标签/备注并删现已无引用的文件；物理 unlink 失败记日志不抛。
- **图片校验/降采样**：非图片（或损坏图片）上传被拒（`attachment.unsupported_type`）；超大上传 → `attachment.file_too_large`；过大图被降采样（记尺寸；封边）；SVG 拒绝；持久化的 `content_type` 是我方校验值。
- **多态属主校验/级联**（§4.3）：坏 `model_type` → `validation.invalid_input`；缺失属主 → 属主的 not-found 码；级联映射清理恰好正确的行。
- **标签**：大小写不敏感重名 → `tag.duplicate_name`；attach 幂等（唯一 link）；`set_tags_for_owner` 整组替换；删标签连带删 link；属主删时 detach-all。
- **备注**：CRUD 限属主；属主删级联；外/缺 id 404。
- **条码**：绑已绑的码（同/异 definition）→ `barcode.duplicate`；definition 删级联其码；`lookup` 返回内部命中，未知码 `found=false`。
- **自定义字段**：有效 map 在 definition + instance 上往返；嵌套/对象值拒绝；键/数量上限；NULL = 无；**可搜索**（一个值子串使属主在全局搜索浮现）。
- **搜索**：各类型子串匹配（definition 含 custom_fields；instance 含 serial + barcode + custom_fields；location/category/tag 按 name）；每类型上限；大小写不敏感；空 `q` 处理。
- **导出**：CSV 表头 + 行；外键扁平为 id+name；tags 连接；custom_fields 序列化；CSV 转义正确（值里的逗号/引号/换行）；JSON 形状匹配；流式（非整体缓冲）。
- **迁移往返**：`0020`–`0027` 在 `0019` 库上干净 upgrade、干净 downgrade；既有行不受影响（新列 NULL）。

**前端**（vitest + Testing Library，mock typed client——M0 风格）：**AttachmentPanel** 上传（mock multipart）、从媒体 URL 渲染画廊、删除；**标签**芯片 + 选择器 attach/detach，列表**标签过滤**收窄；**备注**面板 CRUD；**自定义字段编辑器**增/改/删 KV 行并往返；**条码扫描器**解码（解码器 mock）→ 调 lookup → 已知码路由到物品、未知码提供创建；**全局搜索**输入 → 结果页按类型分组；**导出**按钮触发下载；日期/数字经 M1.5 `formatDate`/`formatQuantity`；所有字符串 **en + zh** 齐备。

---

## 6. 契约优先 codegen 与 no-drift 门

机制不变（M0–M4 §6）。每个碰 API 的步骤**重跑 `make codegen`**并提交 `openapi.json` + `frontend/src/api/schema.d.ts`；CI **contract** job 在漂移时失败。M5 用附件/标签/备注/条码/搜索/导出路径与 schema + `custom_fields` 字段扩充 schema。`/media` 静态挂载是 `include_in_schema=False`（像 SPA 挂载），故不进契约。不碰 schema 的步骤会注明——本里程碑无（所有后端步骤都加端点），故每个后端步骤都重新生成。

---

## 7. 前端设计

### 7.1 可复用的属主元数据组件
三个由 `{ modelType, modelId }` 参数化的组件，落在既有详情面（`InstanceDetail.tsx`、item-definition 详情、`Locations`/`TreeBrowser` 详情）：
- **`AttachmentPanel`** —— Mantine `FileButton`/拖拽上传（图片 + 文档），响应式缩略/预览**画廊**（从 `/media` URL 懒加载），逐项删除，可选标题编辑。非图片附件显示文件图标 + 下载链接。
- **`TagPanel`** —— 着色 `Badge` 芯片 + 标签选择/创建器（在 `GET /tags` 上自动补全，可即时创建），经 `PUT /tags/links` attach/detach。
- **`NotePanel`** —— 备注列表 + 增/改/删。

### 7.2 自定义字段编辑器（`CustomFieldsEditor`）
一个小键值行编辑器（增/删行，键 + 类型化-ish 值输入），嵌进 definition 与 instance 表单弹窗（`DefinitionFormModal`、`InstanceFormModal`）；详情页只读展示。序列化到 `custom_fields` map。

### 7.3 条码扫描 + 入库集成
- 一个 **`BarcodeScanner`** 组件封装 `@zxing/browser`（摄像头权限、1D + 2D 实时解码；手动输入兜底）。解码后 → `GET /barcodes/lookup`：
  - **已知码** → 路由到/预选该 definition（并提供"加一批"——复购快路径）；
  - **未知码** → 提供"创建物品"，把该码预绑为一个 barcode。
- 入库/instance-create 流与 items 页上的一个**扫描入口**；definition 详情上的条码**管理**（列/加/删码）。

### 7.4 全局搜索（`/search` 页 + 头部字段）
- 一个头部搜索 `TextInput`；提交后路由到 **`pages/Search.tsx`** 结果页，调 `GET /search` 并**按类型分组**渲染结果，每行链到其主体（`/items/:id`、`/instances/:id`、`/locations`……）。注册路由 + 一个导航入口。（不引 `@mantine/spotlight` 依赖——纯字段 + 页面。）

### 7.5 导出入口
- 相关列表页（Items、Instances、Locations）上的一个**导出菜单/按钮** → `GET /export/{entity}?format=` 触发文件下载（CSV / JSON）。

### 7.6 i18n
- 新命名空间：**`attachments`**、**`tags`**、**`notes`**、**`customFields`**、**`barcode`**、**`search`**、**`export`**（en + zh），在 `src/i18n/index.ts` 注册；`nav`（搜索）与 `errors`（八个新码）追加。测试钉在 `en`（M1.5）。

### 7.7 Foundations（前端）
- 给 `package.json` + lockfile 加 `@zxing/browser`（+ `@zxing/library`）；更新 `AGENTS.md` 前端依赖行（随 Step 11）。摄像头访问需安全上下文（HTTPS / localhost）——部署/自测注明。

---

## 8. CI & Docker（相对 M4 的增量）

- **新后端依赖**（`python-multipart`、`pillow`）加入 `backend/pyproject.toml` + `uv.lock`；**新前端依赖**（`@zxing/browser`）加入 `package.json` + `pnpm-lock.yaml`；`AGENTS.md` 依赖行更新（Steps 1 / 11）。Pillow 走 manylinux wheel——JPEG/PNG/WebP **无需额外 apt**（HEIC/AVIF 需系统库——延后，§12）。
- **`DATA_DIR` / 媒体目录**：`data_dir` 配置（默认 `./data`）+ 启动时创建 `DATA_DIR/media`。既有单 bind mount（`DATA_DIR → /app/data`）已覆盖媒体——**无新卷**。`/media` 静态挂载注册在 SPA catch-all 之前。
- **Docker**：**migrate** 冒烟步骤现应用 `0001`–`0027`。单镜像其余不变；新东西全零配置（未上传前无媒体）。
- 其余（契约门、缓存、fail-closed `migrate` 服务）与 M0 §8 / M4 §8 完全一致。

---

## 9. 步骤拆解（原子、有序）

每步独立可测、有测试托底、落恰好一个提交（编排模式下一个 per-step autosquash）、碰 API 就重跑 `make codegen`，并继承全局 DoD（§5）+ **反夹带规则**：只实现当前步——不做其他步、不顺手重构、不碰宿主真实环境（不动测试沙箱外的真实 DB/容器/文件）。

> **分相：** A（通用横切元数据）→ B（条码 + 搜索）→ C（导出）→ D（前端）。后端相 A–C 稳定契约；前端相 D 消费它。相内后端步骤大体独立；D 各步依赖其后端对应步。

### 相 A — 通用横切元数据（后端）

**Step 1 — 媒体存储 + 通用附件（+ 依赖、`DATA_DIR`、`/media` 挂载）**
- **建：** 迁移 `0020`（`media_files`）+ `0021`（`attachments`）；`MediaFile`/`Attachment` 模型；`MediaFileRepository`/`AttachmentRepository`；`MediaStorage`（Pillow 校验 + 降采样 + sha256 内容寻址 + 原子写 + 物理删）；`AttachmentService`（上传/列/patch/删带引用计数清理 + `delete_for_owner`）；`OWNER_TYPES` 注册表 + `resolve_owner` 辅助（§4.3）；把 `delete_for_owner` 接进 definition/instance/location 删除服务；`data_dir` 配置 + 启动创建 `media/` + `/media` `StaticFiles` 挂载注册在 SPA catch-all **之前**；`POST/GET/PATCH/DELETE /attachments`；加依赖（`python-multipart`、`pillow`）+ 更新 `AGENTS.md` 后端行；错误码 `attachment.not_found`/`file_too_large`/`unsupported_type`；`make codegen`。
- **测：** 去重；引用计数删（留-vs-删文件）；属主级联；图片校验/降采样；超大/不支持/SVG 拒绝；坏 `model_type`/缺属主；迁移 `0020`/`0021` up/down。
- **提交：** `feat(backend): media storage and generic attachments`

**Step 2 — 标签 + 多态 link**
- **建：** 迁移 `0022`（`tags`）+ `0023`（`tag_links`）；模型、`TagRepository`/`TagLinkRepository`、`TagService`（CRUD + 大小写不敏感重名守卫 + attach/detach + `set_tags_for_owner` + `detach_all_for_owner`）；把级联接进三个删除服务；`GET/POST/PATCH/DELETE /tags`、`GET /tags/links`、`PUT /tags/links`；错误码 `tag.not_found`/`tag.duplicate_name`；`make codegen`。
- **测：** 重名（ci）；attach 幂等；set 替换；删标签连带删 link；属主级联；迁移 up/down。
- **提交：** `feat(backend): flat tags with polymorphic links`

**Step 3 — 备注**
- **建：** 迁移 `0024`（`notes`）；模型、`NoteRepository`、`NoteService`（按属主作用域 CRUD + `delete_for_owner`）；接级联；`GET /notes`、`POST/PATCH/DELETE /notes/{id}`；错误码 `note.not_found`；`make codegen`。
- **测：** CRUD；属主作用域；外 id 404；属主级联；迁移 up/down。
- **提交：** `feat(backend): generic notes`

**Step 4 — definition + instance 上的自定义字段（JSON）**
- **建：** 迁移 `0025`（`item_definitions.custom_fields`）+ `0026`（`stock_instances.custom_fields`）；`CustomFieldsModel` 校验；穿入 definition + instance 的 create/update/response schema；`make codegen`。
- **测：** 有效 map 往返（两实体）；对象/数组值拒绝；键/值/数量上限；NULL 默认；迁移 up/down。
- **提交：** `feat(backend): JSON custom fields on definitions and instances`

### 相 B — 条码 + 搜索（后端）

**Step 5 — 条码 + 商品查询 provider 缝**
- **建：** 迁移 `0027`（`barcodes`）；模型、`BarcodeRepository`、`BarcodeService`（绑/解/列 + 全局唯一守卫）；`ProductLookupProvider` Protocol + `InternalProvider` + `ProductLookupService`；`GET /definitions/{id}/barcodes`、`POST /definitions/{id}/barcodes`、`DELETE /barcodes/{id}`、`GET /barcodes/lookup`；错误码 `barcode.not_found`/`barcode.duplicate`；`make codegen`。
- **测：** 重码（同/异 definition）；definition 删级联；lookup 命中/未命中；provider 链返回首个命中；迁移 up/down。
- **提交：** `feat(backend): barcodes and product-lookup provider`

**Step 6 — 全局搜索 + search-provider 缝**
- **建：** `SearchProvider` Protocol + `LikeSearchProvider` + `SearchService`；`GET /search?q=&types=&limit=` 返回分组结果（definition 含 custom_fields；instance 含 serial+barcode+custom_fields；location/category/tag 按 name）；`make codegen`。
- **测：** 各类型子串匹配含 custom-field 命中 + barcode 命中；每类型上限；大小写不敏感；空/空白 `q`。
- **提交：** `feat(backend): global LIKE search across entities`

### 相 C — 导出（后端）

**Step 7 — CSV / JSON 导出**
- **建：** `ExportService`（stdlib `csv` 流式 + JSON；FK id+name 扁平；tags + custom_fields 列；稳定 Decimal/日期格式）；`GET /export/{entity}?format=`（`StreamingResponse`、`Content-Disposition`）；`make codegen`。
- **测：** CSV 表头+行；FK 名解析；CSV 转义（逗号/引号/换行）；JSON 形状；坏 entity/format → `validation.invalid_input`；流式。
- **提交：** `feat(backend): CSV and JSON data export`

### 相 D — 前端

**Step 8 — 附件面板（上传 / 画廊 / 删除）**
- **建：** instance + definition + location 详情上的 `AttachmentPanel`；上传（multipart）、从 `/media` 懒加载画廊、删除、标题编辑；`attachments` 命名空间（en+zh）。
- **测：** 上传（mock client）、画廊渲染、删除、非图片兜底。
- **提交：** `feat(frontend): attachment upload and gallery`

**Step 9 — 标签 + 备注 UI**
- **建：** 详情面上的 `TagPanel`（芯片 + 选择/创建）与 `NotePanel`；items 列表上的**标签过滤**；`tags` + `notes` 命名空间（en+zh）。
- **测：** attach/detach；即时创建；标签过滤收窄；备注 CRUD。
- **提交：** `feat(frontend): tags and notes UI`

**Step 10 — 自定义字段编辑器**
- **建：** definition + instance 表单弹窗中的 `CustomFieldsEditor`；详情页只读展示；`customFields` 命名空间（en+zh）。
- **测：** 增/改/删行；往返；展示。
- **提交：** `feat(frontend): custom fields editor`

**Step 11 — 条码扫描 + 查询 + 入库集成**
- **建：** `BarcodeScanner`（`@zxing/browser`，摄像头 + 手动兜底）；lookup → 已知路由到物品 / 未知提供创建；入库 + items 的扫描入口；definition 详情上的条码管理；`barcode` 命名空间（en+zh）；加前端依赖 + 更新 `AGENTS.md` 前端行。
- **测：** 解码（mock 解码器）→ lookup → 已知/未知分支；条码增/删。
- **提交：** `feat(frontend): barcode scanning and lookup`

**Step 12 — 全局搜索栏 + 结果页**
- **建：** 头部搜索字段 + `pages/Search.tsx`（分组结果、主体链接）+ 路由 + 导航；`search` 命名空间（en+zh）。
- **测：** 查询 → 分组结果；导航到主体；空状态。
- **提交：** `feat(frontend): global search bar and results page`

**Step 13 — 数据导出 UI**
- **建：** Items/Instances/Locations 上的导出菜单/按钮 → 下载 CSV/JSON；`export` 命名空间（en+zh）。
- **测：** 按钮触发正确的导出 URL/格式（mock）。
- **提交：** `feat(frontend): data export UI`

> 步骤过大可拆（如 Step 1 拆成存储-vs-附件-API，或 Step 9 拆成标签-vs-备注）；保持每步独立绿。后端步骤 1–7 大体独立（任意子集可落）；D 各步依赖其后端对应步（8→1，9→2/3，10→4，11→5，12→6，13→7）。

---

## 10. 盲审检查点（逐步）

审查者只拿到：本文 + roadmap、该步实现简报、该步 diff。检查：

- **Step 1：** 附件是**多态** `(model_type, model_id)`（`model_id` 上无硬 FK）；文件**内容寻址** + **去重**；**引用计数**删在引用归零时删物理文件且**绝不**留孤儿；属主删在服务层**级联**；Pillow 校验 + 降采样 + SVG/超大/不支持拒绝；`content_type` 是我方校验值；`/media` 挂载位于 SPA catch-all **之前**；物理 I/O 是**提交后 best-effort**；依赖 + AGENTS.md + codegen 已提交。
- **Step 2：** 扁平标签；大小写不敏感名唯一 → `tag.duplicate_name`；link 唯一 + 幂等；删标签级联 link；`set_tags_for_owner` 替换；属主删时 detach；codegen 已提交。
- **Step 3：** 备注限属主；外 id → `note.not_found`；属主删级联；codegen 已提交。
- **Step 4：** `custom_fields` 是**扁平**标量 map（嵌套拒绝）；上限强制；穿入**两**个 definition + instance schema；NULL = 无；非追溯（只数据）；codegen 已提交。
- **Step 5：** `code` **全局唯一**（一码对一 definition）；重码 → `barcode.duplicate`；definition 删级联其码；lookup 跑 **provider 链**且 M5 只装**内部**；缝是真的（Protocol）非硬连线；codegen 已提交。
- **Step 6：** LIKE 跨指定类型含 **custom-field + barcode** 命中；每类型上限；**不用 SQLite FTS**（可移植，§2.11）；`SearchProvider` 缝在；codegen 已提交。
- **Step 7：** 导出流式（非整体缓冲）；FK **id+name** 扁平；**CSV 转义**正确；坏 entity/format → `validation.invalid_input`；经服务只读；codegen 已提交。
- **Step 8：** 上传/画廊/删除经**typed client**；画廊来自 `/media` URL；非图片兜底；en+zh 齐备。
- **Step 9：** 标签芯片/选择器 + 备注面板经 typed client；列表上的**标签过滤**；en+zh 齐备。
- **Step 10：** KV 编辑器在两个表单往返 `custom_fields`；en+zh 齐备。
- **Step 11：** 客户端解码（1D+2D）；lookup **已知→路由 / 未知→创建**；入库集成；条码管理；前端依赖 + AGENTS.md；en+zh 齐备。
- **Step 12：** `/search` 分组结果、主体链接、导航；en+zh 齐备。
- **Step 13：** 导出入口打到正确 URL/格式；en+zh 齐备。
- **横切：** 符合 roadmap **§2.8（通用 `model_type+model_id` 横切，非每表）**、§2.9（Decimal/Date）、§2.10（单一 context/仓储层）、§2.11（逻辑在 app 层、**不用 SQLite FTS / 不用 DB JSON 函数**）、§2.12（LLM/识别作**预留缝**——M5 只有内部 provider）；M1.5 统一错误信封（只新增八个码，无裸 `detail`）；本文变更时的**双语文档规则**。

---

## 11. 🟢 部署自测点（拼进 M5 里程碑走查）

里程碑末作者手动走查（展开 roadmap M5 🟢 条目）。假设 M1–M4 运行流（compose up，走 **HTTPS 或 localhost** 以便摄像头可用；以 admin 登录；已有 location 树、category、若干库存）。

1. **贴照片：** 在某物品（或 instance/location）详情，上传一张图片 → 出现在**画廊**。再传**同一张** → 显示但不写第二份文件（去重）。传一个非图片（PDF）→ 显示为可下载文件；传 `.exe`/SVG → 被清晰拒绝。
2. **引用计数删除：** 删该附件 → 画廊清空；最后一个引用移除后底层文件消失（仍被引用的共享文件存活）。
3. **标签：** 建几个标签（带颜色），跨类型贴到物品上；按某标签**过滤** items 列表。
4. **备注：** 给物品加一条备注；编辑并删除。
5. **自定义字段：** 给某 definition 加自定义字段（如 `voltage=220`）与某 instance；详情渲染、编辑往返。
6. **全局搜索：** 搜一个出现在 name、serial、**自定义字段值**、**条码**里的词 → 结果**按类型分组**返回；每条链到主体。切 ZH⇄EN——标签跟随。
7. **绑码 + 扫码：** 给某 definition 绑一个码；然后用摄像头**扫**该码 → 路由到物品并提供快速入库。扫一个**未知**码 → 提供"创建物品"且预绑该码。
8. **导出：** 把 item definition / instance / location 导出为 **CSV** 与 **JSON** → 用表格软件打开 CSV；外键显示为 id **与** name；tags 与自定义字段在内。
9. **备份（手动，延后的功能）：** 停应用，把 bind-mount 的 `DATA_DIR`（SQLite 文件 **+** `media/`）复制到别处，对从该副本恢复的干净目录起服务 → 一切（含照片）回来。（验证延后备份故事成立。）
10. **CI 绿：** 同样的门在 GitHub Actions 通过，含 no-drift 契约门；迁移 `0001`–`0027` 在 docker 冒烟里对全新库干净应用。

---

## 12. 开放问题 / 延后

- **备份/恢复（重新划出）：** M5 依赖复制 `DATA_DIR`。一等公民式的应用内**备份下载 + 恢复**流（以及运行中 SQLite 库的一致快照故事）是后续里程碑/优化候选。
- **CSV 导入（延后）：** 自然归宿是与 **LLM 辅助格式化**后端（§2.12，M9）一起——让模型映射/清洗任意表格列，再校验 + dry-run + 提交。在此之前仅导出。
- **外部商品查询（延到 M9）：** Open Food Facts（免费、无 key；包装食品强，本地/非食品弱）、其姊妹库、ISBN 查询、以及 **LLM 视觉**都实现 M5 的 `ProductLookupProvider` 缝——以 provider + 设置开关追加；扫码流程/UI 不变。
- **LLM 语义搜索（延后）：** `SearchProvider` 缝罩在 LIKE provider 前；语义 provider 日后接（M9 时代）。
- **缩略图（延后）：** M5 以懒加载分发封顶尺寸原图。服务端缩略图流水线（及需系统库的 HEIC/AVIF 支持）是优化。
- **媒体鉴权与内容净化（M6）：** `/media` 上的逐请求鉴权（取代当前 capability-URL 静态挂载）+ 上传净化 + 活动内容处理在 M6 加固轮落地（roadmap §5 M6 / §2 开放问题）。
- **自定义字段结构化：** JSON KV 可作文本搜索，但放弃带类型/范围查询、键一致性分面、逐字段类型化 UI。若实际使用需要结构化筛选，`custom_field_definitions` + 值模型是一个受限的后续。
- **标签颜色/调色板 + 可嵌套标签：** 暂为扁平 + 自由颜色；受管调色板与（若有需要）roadmap 原本的"可嵌套标签"是优化。
- **附件重排序 / 封面图：** `sort_order` 已有；更丰富的拖拽重排 + 指定封面图是 UI 优化。
- **内容哈希碰撞 / 跨属主去重：** sha256 碰撞可忽略；跨属主去重是有意的（一份文件、多个引用），由引用计数处理。
