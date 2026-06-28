# M6 — 多用户与角色

> 🌐 **语言:** [English](./M6.md) · 中文(当前)

> **里程碑设计文档 — 自包含。** 请与 `docs/plan/roadmap.md`(地图,尤其 §5 M6 + 红线 **§1.2 单租户/多用户**、**§2.6 统一提醒引擎、通知到责任人**、**§2.10 单一上下文/仓储层**、**§2.11 逻辑在应用层而非数据库**)以及阐述"为什么存在"的 `docs/inspiration/investigation.md` 一起读。本文是 *M6 构建什么、如何验证* 的唯一真相来源;不要从 roadmap 重新推导范围。进度**只**在 roadmap §4 表中追踪。
>
> 内建约定:原子步骤(§9)、盲审检查点(§10)、🟢 部署自测点(§11),以便手动模式与编排模式都能挂靠本文。
>
> **范围说明(先读)。** M6 把"单管理员"应用变成真正的**多用户家庭/团队**,并偿还 M4/M5 明确推迟到此的安全债。分**五个阶段**(A 角色与权限、B 用户账号与邀请、C 责任人路由与按用户通知偏好、D 审计 + 安全硬化、E 前端)、**13 个原子步骤**交付:**三档 RBAC**(admin / member / viewer)由代码定义的权限矩阵强制;**用户管理**(列出 / 改角色 / 启停 / 删除)带"最后一个管理员"保护;**邀请**(一次性 token 链接,可选邮件发送)+ **自助改密** + **管理员发起的重置密码**;**责任人指派**(物品定义为默认、库存实例为覆盖),**把提醒路由给对的人**(未指定时回退给全体 —— 对 M4 行为零破坏性迁移);**最小化按用户通知偏好**(可退订站内收件箱与/或邮件摘要);一份**安全/管理审计日志**;以及推迟的**硬化轮** —— 认证端点**限流 + 指数退避**、出站 webhook(及未来 MQTT broker 主机)的 **SSRF 守卫**、`/media` 的**按请求会话授权**、以及**滑动窗口会话过期**。

---

## 1. 目标与非目标

**目标(roadmap 对 M6 的承诺 —— 单一家庭内真正多用户 + 硬化):**

- **角色与权限** —— 三个固定角色 **admin / member / viewer**,权限矩阵写在**代码**里(不是 DB 驱动的权限表 —— roadmap §1.2/§2.11),由单一 `require_permission` 路由依赖强制。`admin` = 一切(用户管理、全局设置/通道、审计、全部数据);`member` = 全部数据 CRUD,但无用户管理、无全局设置、无审计;`viewer` = 只读一切。
- **用户管理** —— admin 可列出用户、改角色、停用/启用、删除,全部受**最后一个管理员保护**(家庭永远不会失去最后一个活跃 admin)。列出用户对任何已登录用户开放(供责任人选择器);只有**变更操作**受 admin 限制。
- **邀请 + 密码** —— admin 用邮箱 + 角色邀请新用户;系统铸造**一次性 token** 并返回可复制的**接受链接**(若已配置 SMTP,则**邮件发送**该链接 —— 两条路径共用同一 token)。受邀人打开链接**自设密码**(admin 永远不知道)。外加**自助改密**与**管理员发起的重置密码**(token 链接,同机制)。
- **责任人路由** —— **物品定义**(默认)与**库存实例**(覆盖)上各一个 `responsible_user_id`。提醒引擎把每条提醒路由到批次/定义的**有效责任人**,**未指定时回退给全体活跃用户**(对所有存量数据保留与 M4 完全一致的行为 —— 零破坏性迁移)。这实现了 roadmap 的"基于指派的提醒送达对的人"。
- **按用户通知偏好(最小版)** —— 每个用户可退订**站内收件箱**与/或**邮件摘要**。(HTTP/MQTT 是单一全局端点,没有按用户的含义 —— §2。)
- **审计日志** —— 一份只追加的 `audit_log`,记录**安全/管理事件**(登录成功/失败、登出、用户创建/改角色/停用/删除、改密/重置、邀请签发/接受、设置/通道变更),配 admin 专属查看页。
- **安全硬化**(M4/M5 的推迟项) —— 认证端点**限流 + 指数退避**;出站 webhook 的 **SSRF 守卫**(轻量、常开、**不拦私网 LAN**,以便局域网里的 Home Assistant 继续工作 —— §2);`/media` 的**按请求会话授权**(M5 的 capability-URL 升级为"必须登录");以及**滑动窗口会话过期**(启用 M0 预留的 `sessions.last_seen_at` 钩子)。

**完成判据(🟢,§11 展开):** 通过链接邀请第二个用户、用设密码接受、并以其身份登录;`viewer` 在所有写操作上被拦,而 `member` 能编辑数据但够不到用户管理/设置/审计;给某耐用品指派一个用户,看到它的保修提醒**只**到该用户,而未指派的物品仍到全体;某用户退订邮件摘要后不再收到(但站内收件箱仍在);审计日志显示这些登录、邀请、改角色;暴力破解登录会指数退避;无会话访问 `/media` 返回 401;出站 webhook 拒绝 `http://169.254.169.254/…`。CI 保持绿,含无漂移契约门;迁移 `0001`–`0032` 在全新库上干净应用。

**非目标(明确排除出 M6 —— 推迟或后续里程碑):**
- **不做自定义 / DB 驱动角色或按资源 ACL。** 三角色 + 代码矩阵是既定粒度(roadmap §7)。`custom_role` / 细粒度权限表是 parking-lot 改良项。
- **不做按行数据归属 / 访问范围。** 这是单一家庭、**所有用户共享全部数据**(roadmap §1.2)。责任人纯粹是**提醒路由**提示,**不是**访问控制边界 —— `member` 仍可编辑任何物品,无论谁负责。
- **不做超出两个退订之外的按用户*节奏*/按通道路由。** M4 §12 的完整"每用户 × 每通道 × 每计划"矩阵继续搁置;M6 只发站内 + 邮件摘要两个退订(§2,作者的"最小版")。
- **不做 OIDC / SSO / 2FA / 注册时邮箱验证。** 认证仍是 opaque 会话 cookie(roadmap parking lot)。邀请链接*即*信任路径;没有单独的邮箱确认步骤。
- **不做 SSRF 私网拦截、不做 DNS-rebinding IP 钉死、不做 allow-list。** M6 的守卫拦 loopback/链路本地/云元数据/保留目标,但**放行私网 LAN**(自托管 + Home-Assistant-on-LAN 的核心用法)。更严模式见 §12。
- **不做全局 API 限流。** 只限未认证/认证端点;其余 API 已由会话 token 把守(作者的决定 —— §2)。
- **不做分布式/持久化限流存储。** 限流器是**进程内、单进程**(匹配单容器 SQLite 部署);重启即重置(§12)。
- **不审计普通数据变更。** 库存变更已由 M2 出入库账本审计;通用数据变更审计排除(§2/§12)。

---

## 2. 锁定决策(M6 规划期间敲定;理由见规划讨论 + roadmap §1.2/§2)

| 领域 | 决策 |
|---|---|
| **三固定角色 + 代码权限矩阵** | 角色就是现有 `users.role` **String(64)** 列,应用层校验属于 `{"admin","member","viewer"}`(无 DB enum/CHECK —— §2.11)。一个模块级 `PERMISSIONS: dict[Role, set[Permission]]` 把每个角色映射到一小组 **Permission**(`VIEW`、`EDIT`、`MANAGE_USERS`、`MANAGE_SETTINGS`、`VIEW_AUDIT`)。`require_permission(perm)` FastAPI 依赖工厂解析当前用户,角色缺少 `perm` 时抛 **403 `auth.forbidden`**。角色列**不需迁移**(已存在;引导 admin 已是 `"admin"`)。 |
| **权限映射(矩阵)** | `viewer → {VIEW}`;`member → {VIEW, EDIT}`;`admin → {VIEW, EDIT, MANAGE_USERS, MANAGE_SETTINGS, VIEW_AUDIT}`。**自助永不被权限拦**:任何已登录用户都可读/改*自己*的资料(`/auth/me`、偏好、改自己密码)与*自己*的通知收件箱,无关角色。**列出**用户是 `VIEW`(责任人选择器需要);只有用户*变更*才是 `MANAGE_USERS`。 |
| **强制 = 在现有路由上做增量扫描** | 今天每个已登录用户实质都是 admin(无检查)。M6 给每个**数据写**路由(locations/categories/item kinds/definitions/instances/movements/attachments/tags/notes/barcodes/custom-fields/责任人 上的 POST/PATCH/PUT/DELETE)加 `Depends(require_permission(EDIT))`。读路由不变(已登录即满足 `VIEW`)。设置/通道 + `POST /reminders/run` → `MANAGE_SETTINGS`;用户变更 + 邀请 + 重置 → `MANAGE_USERS`;`GET /audit` → `VIEW_AUDIT`。 |
| **邀请:在 `user_tokens` 留待定行,接受时才建用户** | 邀请**不**创建半成品 `users` 行。一个 `user_tokens` 行(`purpose="invite"`、`email`、`role`、`token_hash`、`expires_at`)保存待定邀请;`users` 行在**接受时**创建(带所选密码,`is_active=True`)。原始 token 仅**一次性**返回给 admin(用于可复制链接),落库**哈希**(sha256),所以 DB 泄露不会暴露可用链接。可选 SMTP 发送复用 M4 邮件传输。 |
| **管理员重置 + 自助改密共用 token 表** | `user_tokens.purpose="password_reset"`(带 `user_id`)支撑管理员发起的重置(一次性链接)。**自助改密**(`POST /auth/change-password`,校验当前 → 设新)不需 token。一张 token 表、两种用途 —— DRY。 |
| **责任人同时在定义(默认)+ 实例(覆盖)** | **`item_definitions`** 和 **`stock_instances`** 上各一个可空 `responsible_user_id` FK(`ondelete=SET NULL`)。批次的**有效**责任人 = `instance.responsible_user_id` → 否则 `definition.responsible_user_id` → 否则**无**。(与 M4 lead-time 链同形:覆盖→默认→无。)`SET NULL` 意味着删用户会干净地把其指派回退到 fallback。 |
| **提醒路由 + 回退全体(零破坏性)** | `best_before` / `warranty`(按批次)路由到**批次**有效责任人;`low_stock`(按定义)路由到**定义**责任人。**有效责任人为 `None` 时,该源回退给*全体活跃用户*** —— 即 M4 行为。因为每个存量行都从未指派开始,M6 上线表现与 M4 完全一致,仅在家庭逐步指派后才收窄。 |
| **按用户通知偏好:两个退订** | `users` 上两个布尔 —— `notify_in_app`(默认 true)与 `notify_email_digest`(默认 true)。对一个被路由的收件人,**当且仅当**其想要*至少一个*通道时才创建 `notifications` 行(保住去重账本 + 邮件源完整);站内收件箱查询受 `notify_in_app` 把守;邮件摘要跳过 `notify_email_digest=False` 的用户。**HTTP/MQTT 是全局单端点 → 无按用户偏好**(无论如何每条通知只发一次)。经现有 `PATCH /auth/me` 暴露。 |
| **审计日志:只追加、仅安全/管理事件** | 一张 `audit_log` 表由 `AuditService` 从认证/用户/设置代码路径写入。只追加(应用不更新/删除)。`actor_user_id`(`SET NULL`)**外加一个去规范化 `actor_email` 快照**,使失败登录(无用户)与删除后的记录仍可读。admin 专属 `GET /audit` 带过滤。普通数据变更**不**审计(出入库账本已覆盖库存)。 |
| **限流:仅认证端点、进程内、指数退避** | 一个小型进程内 `RateLimiter`,按 `(scope, client_ip[, email])` 把守 `login`、`setup`、`change-password`、两个 token-`accept` 端点。超过每窗口尝试阈值后施加**指数增长锁定**(每次违规翻倍,封顶),返回 **429 `auth.rate_limited`** 带 `Retry-After`;成功清零计数。单进程存储(匹配单容器);重启重置。其余 API 不限流(已会话把守 —— 作者决定)。 |
| **SSRF 守卫:轻量、常开、放行 LAN** | 一个 `validate_outbound_url(url)` 在 webhook POST 前运行:scheme ∈ `{http,https}`、host 非空,然后**解析主机并拒绝 loopback(127/8、::1)、链路本地(169.254/16 含 `169.254.169.254`、fe80::/10)、未指定、多播、保留** —— 但**放行**私网 LAN(10/8、172.16/12、192.168/16、fc00::/7)。webhook 调用还设 `follow_redirects=False` 与硬超时。更轻的 `validate_broker_host` 对(当前禁用的)MQTT host 套同一拦截表。理由 + 公网 IP/Home-Assistant 分析见 §4.7。 |
| **`/media` 按请求会话授权** | M5 的 `/media/{shard}/{digest}` 路由加 `Depends(get_current_user)` → **无有效会话即 401**。浏览器对同源 `<img>`/下载请求自动带会话 cookie,图库照常工作。单一家庭里所有已登录用户共享全部数据,"必须登录"*即*足够授权(含 viewer)。路由保持 `include_in_schema=False`。 |
| **滑动窗口会话过期** | `sessions.verify()` 启用预留的 `last_seen_at`:有效请求时,若剩余不足半个 TTL,则刷新 `expires_at = now + TTL` 与 `last_seen_at = now`(节流,使大多数请求不写)。活跃用户保持登录;闲置会话仍过期。TTL 保持 M0 的 24h(后续可由 operator 调)。 |
| **错误码(新增)** | 新增:`auth.forbidden`(403)、`auth.rate_limited`(429)、`auth.invalid_token`(400 —— 邀请/重置 token 无效/过期/已用)、`auth.password_incorrect`(400 —— 当前密码错)、`user.not_found`(404)、`user.email_exists`(409 —— 邮箱已是用户)、`user.last_admin`(409 —— 会移除最后一个活跃 admin)、`invitation.not_found`(404 —— 撤销不存在的邀请)。**八个**新 `ErrorCode`,全部走 M1.5 统一错误信封。 |

> 这些扩展 M0–M5 基础。新表/列、每个新 schema、新端点都经现有无漂移契约门(§6)与 M1.5 统一错误信封。无新运行时依赖(限流器、SSRF 守卫、token 哈希都用标准库 + 已有的 `argon2`/`httpx`)。`/media` 授权改动与角色检查是唯一"基础"触点,且不需改 `AGENTS.md` 依赖行。

---

## 3. 数据模型

三张新表 + 四个加列增量。全部遵循 M0 约定:共享 `Base` 上 SQLAlchemy 2.0 类型化 `Mapped[...]`,规则在服务层,DB 访问**只**经仓储。列增量都是可空或带 server-default 的平凡加列(SQLite batch-mode `add_column`,不重建表)。**当前 head = `0027`;M6 新增 `0028`–`0032`。**

### 3.1 `user_tokens` —— 新表(迁移 `0028`)

邀请与管理员重置密码的一次性 token。

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `purpose` | String(32) | 否 | `invite` / `password_reset`(应用校验;无 DB CHECK —— §2.11)。 |
| `email` | String(254) | 是 | 受邀邮箱(仅邀请;小写)。重置时 NULL。 |
| `role` | String(64) | 是 | 被邀角色(仅邀请)。重置时 NULL。 |
| `user_id` | FK→users.id | 是 | 目标用户(仅重置)。`ondelete=CASCADE`。邀请时 NULL。 |
| `token_hash` | String(64) | 否 | **唯一**(`uq_user_tokens_token_hash`)。原始 token 的 sha256;原始 token 永不落库。 |
| `expires_at` | DateTime(tz) | 否 | 硬过期(邀请默认 7 天、重置默认 24h;常量)。 |
| `consumed_at` | DateTime(tz) | 是 | NULL = 仍可用;接受时置位(一次性)。 |
| `created_by` | FK→users.id | 是 | 签发的 admin。`ondelete=SET NULL`。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

索引:唯一 `(token_hash)`;`(email)`(每邮箱仅一条待定邀请的检查)。"待定" = `consumed_at IS NULL AND expires_at > now`。

### 3.2 `audit_log` —— 新表(迁移 `0032`)

只追加的安全/管理事件日志。

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `id` | Integer | 否 | PK。 |
| `event_type` | String(64) | 否 | 例如 `auth.login_succeeded`、`auth.login_failed`、`auth.logout`、`user.created`、`user.role_changed`、`user.deactivated`、`user.reactivated`、`user.deleted`、`password.changed`、`password.reset`、`invitation.issued`、`invitation.accepted`、`invitation.revoked`、`settings.changed`。 |
| `actor_user_id` | FK→users.id | 是 | 操作者;`ondelete=SET NULL`。失败登录/系统动作为 NULL。 |
| `actor_email` | String(254) | 是 | **去规范化快照**,使失败登录(无用户)与删除后的行仍可读。 |
| `target_type` | String(32) | 是 | 例如 `user`、`invitation`、`setting`。 |
| `target_id` | Integer | 是 | 受影响实体 id(无硬 FK —— 多态)。 |
| `params` | Text | 是 | JSON 对象字符串,结构化细节(如 `{"old_role":"member","new_role":"admin"}`)。 |
| `ip_address` | String(64) | 是 | 当时的客户端 IP。 |
| `created_at` | DateTime(tz) | 否 | `server_default=now()`。 |

索引:`(created_at)`、`(event_type)`、`(actor_user_id)`。

### 3.3 `users` —— 列增量(迁移 `0031`)

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `notify_in_app` | Boolean | 否 | `server_default=true`。False = 排除站内收件箱。 |
| `notify_email_digest` | Boolean | 否 | `server_default=true`。False = 对该用户跳过邮件摘要。 |

(`role` 列已自 `0002` 存在;M6 只在应用层约束其取值集 —— **不需迁移**。)

### 3.4 `item_definitions.responsible_user_id`(迁移 `0029`)

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `responsible_user_id` | FK→users.id | 是 | `ondelete=SET NULL`。该定义下批次的默认责任人。 |

索引 `(responsible_user_id)`。

### 3.5 `stock_instances.responsible_user_id`(迁移 `0030`)

| 列 | 类型 | 可空 | 备注 |
|---|---|---|---|
| `responsible_user_id` | FK→users.id | 是 | `ondelete=SET NULL`。对定义默认的按批次覆盖。 |

索引 `(responsible_user_id)`。

### 3.6 迁移清单

| Rev | 步骤 | 做什么 |
|---|---|---|
| `0028` | 3 | 建 `user_tokens`。 |
| `0029` | 4 | `item_definitions`:加 `responsible_user_id`。 |
| `0030` | 4 | `stock_instances`:加 `responsible_user_id`。 |
| `0031` | 5 | `users`:加 `notify_in_app` + `notify_email_digest`。 |
| `0032` | 6 | 建 `audit_log`。 |

全部用平凡的 `op.create_table`/`op.drop_table` 与增量的 SQLite batch-mode `add_column`/`drop_column`(M0 约定)可逆。无数据回填:布尔列 server-default `true`(存量用户保留两通道 = M4 行为);FK 列默认 NULL(一切未指派 → 回退全体)。

---

## 4. 后端设计

### 4.1 分层(扩展 M0–M5)

- **RBAC 核心**(`app/auth/permissions.py`,新):`Role` 常量、`Permission` 常量、`PERMISSIONS` 映射、`has_permission(role, perm) -> bool`。
- **依赖**(`app/api/deps.py`):新 `require_permission(perm)` 工厂,返回一个依赖,调 `get_current_user`、检查 `has_permission(user.role, perm)`,否则抛 `auth.forbidden`/403(返回 `User` 供路由复用)。便捷别名 `require_edit`、`require_manage_users`、`require_manage_settings`、`require_view_audit`。
- **仓储**(`app/repositories/`):扩展 `UserRepository`(列全部含停用、设角色、设活跃、删除、统计活跃 admin、设通知偏好、设密码哈希、公共可选列表);新 `UserTokenRepository`(创建、按 token_hash 取、按邮箱取待定邀请、列待定、标记已用、删除、清过期);新 `AuditLogRepository`(追加、按过滤 + 分页列出)。
- **服务**(`app/services/`):
  - `UserAdminService` —— 列/取/改角色/启停/删,带**最后管理员保护**(`count_active_admins()` 在 降级/停用/删除 后必须保持 ≥ 1,否则 `user.last_admin`)。发审计事件。
  - `InvitationService` —— 创建邀请(重复邮箱 → `user.email_exists`;撤销该邮箱任何先前待定邀请;铸 token;可选 SMTP)、列/撤销、**接受**(校验 token → 建用户 → 标记已用 → 审计)。`password_reset` 变体。
  - `PasswordService` 辅助 —— 自助改密(校验当前 → 不符 `auth.password_incorrect` → 设哈希 → 审计;可选撤销该用户*其它*会话)、管理员重置(签发 token)、接受重置(设哈希、标记已用、审计)。
  - `AuditService` —— `record(event_type, *, actor, target_type, target_id, params, request)` 构造行(取 `actor_email` + `ip_address`);`list(filters)`。
  - `RateLimiter`(`app/core/rate_limit.py`,新)—— 进程内 `check(scope, key)` / `register_failure` / `clear`;指数退避策略(§4.6)。
  - 出站 URL/host 校验(`app/core/net_guard.py`,新)—— `validate_outbound_url`、`validate_broker_host`(§4.7)。
- **提醒引擎**(`app/services/reminder_engine.py`)—— 收件人解析改动(§4.4)与按用户偏好把守(§4.5)。通知*模型*不变。

### 4.2 RBAC 强制(增量扫描)

`require_permission` 与现有 `get_authenticated_context`/`get_current_user` 并列插入。具体地,一个写路由多一个参数:

```python
@router.post("/locations")
def create_location(
    body: LocationCreate,
    ctx: RequestContext = Depends(get_authenticated_context),
    _: User = Depends(require_permission(Permission.EDIT)),
) -> LocationResponse:
    ...
```

扫描(步骤 1)是**机械式**:每个现有数据路由的 POST/PATCH/PUT/DELETE 都加 `require_permission(EDIT)`;读路由不变(已登录即满足 `VIEW`)。设置/通道 + `POST /reminders/run` 得 `MANAGE_SETTINGS`。新的 用户/邀请/审计 路由各自声明。**自助路由(`/auth/me`、改密、本人通知收件箱)不加权限门。** 盲审者检查*没有*遗漏任何写路由(viewer 必须无法写任何东西)且*没有*过度把守读路由(viewer 必须能读一切)。

### 4.3 邀请、密码与 token 生命周期

- **创建邀请**(`POST /invitations`,`MANAGE_USERS`):校验邮箱不是已有用户(`user.email_exists`);撤销该邮箱任何先前待定邀请;铸 `token = secrets.token_urlsafe(32)`,存 `sha256(token)`;构造 `accept_url = <app_origin>/invite/accept?token=<token>`。若配置 SMTP,则邮件发送(尽力而为;失败不致请求失败 —— 链接仍返回)。**响应含原始 `accept_url`**(admin 可信)+ `emailed: bool`。审计 `invitation.issued`。
- **接受邀请**(`POST /invitations/accept`,**公开**、限流):`{token, password}` → 哈希 token,查待定行 → 否则 `auth.invalid_token`;建用户(`email`、`role`、`password_hash`、`is_active=True`);置 `consumed_at`;审计 `invitation.accepted`。**不**自动登录(与 setup 一致)。配套 `GET /invitations/accept?token=` 校验并返回 `{email, role}` 以渲染表单。
- **管理员重置**(`POST /users/{id}/reset-password`,`MANAGE_USERS`):为该用户铸一个 `password_reset` token;返回 `reset_url`;可选 SMTP。审计 `password.reset`(签发)。
- **接受重置**(`POST /password-reset/accept`,**公开**、限流):`{token, password}` → 校验 → 设该用户 `password_hash` → 标记已用 → 审计 `password.reset`(完成)。可选撤销该用户现有会话。
- **自助改密**(`POST /auth/change-password`,任意已登录):`{current_password, new_password}` → 校验当前(不符 `auth.password_incorrect`)→ 设哈希 → 审计 `password.changed` → 撤销该用户*其它*会话(保留当前)。
- **token 卫生**:`user_tokens` 的 `purpose` 过期清理在启动 lifespan 中与现有会话清理并列运行。

### 4.4 提醒收件人路由(易错逻辑)

把无条件的 `recipients = self._user_repo.list_active()`(引擎第 306 行)替换为**按源、按主体**解析:

```
effective_responsible(lot):                       # 用于 best_before / warranty
    return lot.responsible_user_id
        or lot.definition.responsible_user_id
        or None

effective_responsible(definition):                # 用于 low_stock
    return definition.responsible_user_id or None

recipients_for(subject):
    rid = effective_responsible(subject)
    if rid is not None and 用户活跃:
        return [该用户]
    return 全体活跃用户                            # 回退 == M4 行为
```

- **日期源**(`_evaluate_date_source`)**按批次**计算 `recipients_for(lot)`(不是一个全局收件人列表),然后对正好这些收件人跑现有的按 (user, lot, date) 去重/窗口逻辑。
- **低库存**路径(`_evaluate_low_stock_for_user`)对每个低库存定义遍历 `recipients_for(definition)`。事件触发路径(`evaluate_low_stock`)对那一个范围内定义用同样解析。
- 一个**停用**或被删除(`SET NULL`)的责任人会坍缩到回退 —— 绝不丢提醒。
- **lead-time 解析不变**(per-item → per-user → global,M4 §4.3):一旦选定收件人,其按用户 lead 覆盖仍适用。

### 4.5 按用户通知偏好把守

- **建行**:引擎中,在为收件人 `U` 写 `notifications` 行前,若 `not U.notify_in_app and not U.notify_email_digest`(什么都不想要)则整个跳过 `U`。否则建行(它仍是去重账本 + 邮件源)。
- **站内收件箱**:收件箱列出端点(M4)仅当当前用户 `notify_in_app=True` 时返回行;关闭时收件箱读空、UI 隐藏铃铛。(行可能仍存在以喂邮件摘要 —— 一个刻意的、有记录的简化,§12。)
- **邮件摘要**:`EmailChannel._deliver_to_recipient` 跳过 `notify_email_digest=False` 的收件人(一行过滤;摘要已按 `user_id` 分组,email.py 第 147 行)。
- **HTTP/MQTT** 分发不动(全局,每条通知发一次)。

### 4.6 限流 + 指数退避

`RateLimiter`(进程内、单进程)按 `(scope, key)` 追踪近期失败计数与锁定截止时间:

- **Key**:`login`/`setup`/token-accept 用 `client_ip`;`change-password` 用 `(client_ip, user_id)`。客户端 IP = `request.client.host`(反代后的 X-Forwarded-For 处理是有记录的部署注意项 —— §12)。
- **策略**:每滚动窗口(默认 5 分钟)允许至多 `N` 次失败(默认 5);超过后,**每次后续违规翻倍的锁定** —— `base_lockout * 2^(violations - 1)`,封顶(如 base 30s、cap 30 分)。**成功清零**计数。
- **强制**:一个 `auth_rate_limit(scope)` 依赖在处理器*之前*调 `check()`;锁定时抛 `auth.rate_limited`/429,带 `Retry-After` 头与 `params={"retry_after_seconds": n}`。处理器在凭证错/token 错时调 `register_failure()`,成功时调 `clear()`。
- 失败登录还往审计日志写 `auth.login_failed`。

### 4.7 SSRF 守卫(出站)—— 以及 Home-Assistant 分析

**唯一出站抓取在哪。** 应用只发一种出站 HTTP 请求:**webhook 通知器**(`app/notifications/channels/http.py`,第 228 行 POST)。MQTT 是*连接* broker(另一种协议,当前禁用)。入站 HA state 端点是入站,不是 SSRF。

**为何要守卫(诚实范围)。** 经典 SSRF 之所以咬人,是因为*不可信用户*提交服务器去抓的 URL。Omniventory 一个这种入口都没有 —— 唯一被抓的 URL 是 **admin 配置的** webhook,而 M6 之后只有 `MANAGE_SETTINGS`(admin)能设它。所以实战风险**低**;守卫是**纵深防御**,价值主要在:(a) 云部署(挡 `169.254.169.254` 元数据端点 → 防偷实例凭证),(b) 抓配置错误,(c) 限制被攻陷 admin 会话的爆炸半径。这就是 M6 上**轻量、常开**守卫而非更严模式的原因。

**Home Assistant on LAN 是一等目标,所以守卫不拦私网。** 作者经**公网 IP** 访问 HA(hairpin NAT 把公网主机名环回 LAN)—— 对应用而言 webhook 解析为*公网* IP,轻松通过;即便严格拦私网也不影响该设置。但开源受众常直接指向 `192.168.x.x`,所以**放行私网 LAN**。

**`validate_outbound_url(url)`**:scheme ∈ `{http,https}` 且 host 非空 → 否则拒;解析主机(`socket.getaddrinfo`),若**任一**解析 IP 是 loopback / 链路本地(含 `169.254.169.254`)/ 未指定 / 多播 / 保留(Python `ipaddress` 分类)则拒;**放行**私网 LAN。webhook 调用加 `follow_redirects=False`(302→内网无法偷渡)并保留现有硬超时。`validate_broker_host(host)` 对 MQTT broker 主机套同一拦截表(轻量;MQTT 已禁用)。残留的 DNS-rebinding TOCTOU(解析后再连接)鉴于 admin 配置的静态配置可接受(§12)。

### 4.8 `/media` 授权与会话滑动窗口

- **`/media`**:给现有 `GET /media/{shard}/{digest}` 路由(main.py 约 382 行)加 `Depends(get_current_user)` → 无会话即 401。同源 `<img>`/下载请求自动带 cookie;图库不受影响。`include_in_schema=False` 保留。
- **滑动窗口**:`sessions.verify()`(auth/sessions.py)—— 对有效会话,若 `expires_at - now < TTL/2`,设 `expires_at = now + TTL` 与 `last_seen_at = now`,再 `flush`。半衰期判断节流,使绝大多数请求不写。该刷新即使在只读路由上也必须为该请求**提交**(在依赖 / `get_db` teardown 处理 —— §5 标注为易错)。

### 4.9 API 面(增量;全部在 `settings.api_prefix` 下,默认 `/api`)

| 方法 + 路径 | 鉴权 | 用途 |
|---|---|---|
| `POST /auth/change-password` | 自助(任意已登录) | `{current_password, new_password}`;400 `auth.password_incorrect`;撤销其它会话。限流。 |
| `GET /users` | session(`VIEW`) | 列出用户(id、email、role、is_active)—— 喂 admin 页**与**责任人选择器。 |
| `GET /users/{id}` | `MANAGE_USERS` | 单个用户(完整)。404 `user.not_found`。 |
| `PATCH /users/{id}` | `MANAGE_USERS` | 改 `role` 与/或 `is_active`。最后管理员保护 → 409 `user.last_admin`。 |
| `DELETE /users/{id}` | `MANAGE_USERS` | 删用户。最后管理员保护。 |
| `POST /users/{id}/reset-password` | `MANAGE_USERS` | 签发重置链接;返回 `reset_url` + `emailed`。 |
| `POST /invitations` | `MANAGE_USERS` | `{email, role}` → `{id, email, role, expires_at, accept_url, emailed}`。409 `user.email_exists`。 |
| `GET /invitations` | `MANAGE_USERS` | 列待定邀请。 |
| `DELETE /invitations/{id}` | `MANAGE_USERS` | 撤销。404 `invitation.not_found`。 |
| `GET /invitations/accept?token=` | **公开** | 校验 + 返回 `{email, role}`。400 `auth.invalid_token`。 |
| `POST /invitations/accept` | **公开**、限流 | `{token, password}` → 建用户。 |
| `GET /password-reset/accept?token=` | **公开** | 校验(返回掩码邮箱)。400 `auth.invalid_token`。 |
| `POST /password-reset/accept` | **公开**、限流 | `{token, password}` → 设密码。 |
| `GET /audit?event_type=&actor_id=&from=&to=&limit=&offset=` | `VIEW_AUDIT` | 分页审计列表。 |
| `POST /definitions` · `PATCH /definitions/{id}` | `EDIT` | 现在也接受/返回 **`responsible_user_id`**。 |
| `POST /instances` · `PATCH /instances/{id}` | `EDIT` | 现在也接受/返回 **`responsible_user_id`**。 |
| `PATCH /auth/me` | 自助 | 现在也接受 **`notify_in_app`**、**`notify_email_digest`**(与 M1.5/M4 偏好并列)。 |
| *(现有数据写)* | `EDIT` | locations/categories/item-kinds/definitions/instances/movements/attachments/tags/notes/barcodes 加 `require_permission(EDIT)`。 |
| *(现有设置 + `POST /reminders/run`)* | `MANAGE_SETTINGS` | 加设置权限。 |

### 4.10 Schema(`app/schemas/`)

- `UserResponse` 已带 `role` + `is_active`;加 `notify_in_app` + `notify_email_digest`。一个轻量 `UserSummary`(`id, email, role, is_active`)供 `GET /users` + 选择器。
- `UserAdminUpdate`(`role?`、`is_active?` —— `model_fields_set` PATCH 语义;role 校验属于角色集)。
- `InvitationCreate`(`email`、`role`);`InvitationResponse`(`id, email, role, expires_at, accept_url, emailed`);`InvitationPublic`(`email, role`)供 GET-accept;`InvitationAccept`(`token, password`)。
- `PasswordChange`(`current_password`、`new_password`);`PasswordResetIssueResponse`(`reset_url, emailed`);`PasswordResetAccept`(`token, password`)。
- `AuditLogResponse`(`id, event_type, actor_email, target_type, target_id, params, ip_address, created_at`)+ 分页信封。
- `UserPreferencesUpdate`(M1.5/M4)扩展 `notify_in_app?`、`notify_email_digest?`。
- 定义/实例的 create/update/response schema 扩展 `responsible_user_id`(+ 响应上可选的已解析 `responsible_user` 摘要)。
- 密码字段复用现有 setup/login 的密码校验(最小长度等)。

---

## 5. 质量门与易错逻辑(完成定义)

继承 M0–M5 §5(`make check` 全绿;build;`make codegen` 无漂移)。M6 中**必须有单测**的逻辑:

**后端**
- **权限矩阵与强制**(§4.2 —— 必需):对每个角色 × 每类被门控路由,允许/拒绝恰好等于矩阵;`viewer` 在**每个**数据写上被拒(403 `auth.forbidden`)、在每个读上被允许;`member` 在数据上允许、在 用户/设置/审计 上被拒;`admin` 处处允许;自助路由(`/auth/me`、改密、本人收件箱)对所有角色允许。
- **最后管理员保护**(§4.1):降级 / 停用 / 删除**最后一个活跃 admin** → `user.last_admin`;存在第二个活跃 admin 时同操作成功;会把家庭弄成孤儿的自降级被拦。
- **邀请与 token**(§4.3):为已有邮箱邀请 → `user.email_exists`;新邀请撤销该邮箱先前待定的那条;接受用被邀角色 + 可用密码建用户;**已用**、**过期**、**未知** token → `auth.invalid_token`;token **哈希**落库(原始 token 永不进 DB);`purge_expired` 清陈旧行。
- **密码**:自助改密当前错 → `auth.password_incorrect`;正确改密更新哈希并撤销*其它*会话但保留当前;管理员重置签发可用 token;接受重置设新哈希并标记已用。
- **责任人路由**(§4.4 —— 必需):实例覆盖胜过定义默认;批次未设时用定义默认;**未指派 → 全体活跃用户**(M4 对齐);**停用/删除**的责任人坍缩到回退(不丢提醒);低库存按定义责任人路由;选定收件人后 lead-time 解析仍适用。
- **按用户通知偏好**(§4.5):`notify_email_digest=False` 只移除该用户摘要(站内行完好);`notify_in_app=False` 清空该用户收件箱;两者皆关的用户拿不到通知行;默认(两者 true)精确复现 M4。
- **限流**(§4.6):窗口内 N 次失败被允许,下一次被锁;锁定每次违规**翻倍**且封顶;成功**清零**计数;锁返回 429 + `Retry-After`;限流器按 `(scope, key)`(一个 IP 的锁不影响另一个)。
- **SSRF 守卫**(§4.7):`http://127.0.0.1`、`http://169.254.169.254`、`http://[::1]`、解析到 loopback 的主机名、`ftp://` scheme、缺 host 都被**拒绝**;公网主机**和**私网 LAN 主机(`192.168.x`、`10.x`)被**放行**;webhook 调用设 `follow_redirects=False`。
- **`/media` 授权**:无会话请求 → 401;有有效会话 → 文件(正确 `content_type` + `nosniff`)。
- **会话滑动窗口**:刷新阈值内的请求延长 `expires_at`/`last_seen_at`(且变更被提交);TTL 余量充裕的请求**不**写;过期会话仍 401。
- **审计日志**:每个被覆盖事件恰写一行,带正确 `event_type`、`actor_email` 快照、target、params;失败登录记录 `actor_user_id=NULL` 但 `actor_email` 有值;列出端点过滤 + 分页;admin 专属。
- **迁移往返**:`0028`–`0032` 在 `0027` 库上干净升级、干净降级;存量行不受影响(布尔默认 true、FK NULL)。

**前端**(vitest + Testing Library,mock 类型化 client —— M0 风格):角色门控的 nav/路由按角色渲染(mock `user.role`);写操作按钮对 `viewer` 隐藏/禁用;**Users** 页列出、改角色、切活跃、删除(浮现最后管理员错)并创建邀请(显示链接 + 复制 + 可选"发邮件");**邀请接受**与**重置接受**公开页设密码后跳登录;**改密**校验并浮现 `auth.password_incorrect`;**责任人选择器**在定义 + 实例表单上设/清指派;**通知偏好**开关经 `PATCH /auth/me` 往返;**审计**页列出 + 过滤;**限流** 429 浮现清晰的"N 秒后再试";所有新串 **en + zh** 齐全;日期/数字经 M1.5 `formatDate`/`formatQuantity`。

---

## 6. 契约优先 codegen 与无漂移门

机制不变(M0–M5 §6)。每个触碰 API 的步骤**重跑 `make codegen`** 并提交 `openapi.json` + `frontend/src/api/schema.d.ts`;CI **contract** 任务在漂移时失败。M6 用 用户管理/邀请/密码/审计 路径与 schema、定义+实例上的 `responsible_user_id`、用户偏好上的两个通知字段扩展 schema。`/media` 路由保持 `include_in_schema=False`,故其新授权依赖不改契约。公开 token-accept 端点**在**schema 内(前端经类型化 client 调用)。

---

## 7. 前端设计

### 7.1 鉴权上下文 + 角色门控(`AuthContext`)
把 `App.tsx` 当前按 prop 传递的 `user` 提升进一个小型 **`AuthContext`**,暴露 `{ user, role, can(permission), refresh, logout }`,并在客户端镜像权限矩阵(`can('EDIT')`、`can('MANAGE_USERS')`…),使 UI 能隐藏后端将拒绝的东西。门控点:
- **Nav**(`AppShell`):`Configuration`、`Users`、`Audit` 项只对 `admin` 渲染;其余对所有角色。
- **路由**:admin 专属路由(`/users`、`/audit`、`/configuration`)对非 admin 重定向到仪表盘;现有数据路由保持开放(读)。
- **写操作**:`!can('EDIT')`(viewer)时,创建/编辑/删除按钮与出入库/消耗/移动控件隐藏(或禁用带提示)。后端仍是真相来源(纵深防御)。
- 资料/用户按钮显示当前**角色**。

### 7.2 用户管理页(`pages/Users.tsx`,admin)
一张用户表(email、role、active、created),带:改角色(select)、启停切换、删除(确认)—— 全部清晰浮现 `user.last_admin` —— 以及一个**邀请**动作:一个取 email + role 的模态,然后展示返回的**接受链接**带复制按钮,且配置 SMTP 时一个**"发邮件"**确认。一个带撤销的**待定邀请**列表。每用户一个**"重置密码"**动作,展示重置链接(+ 可选邮件)。

### 7.3 公开 token 页(`pages/AcceptInvite.tsx`、`pages/ResetPassword.tsx`)
**预登录**页(像 Login/Setup)。`App.tsx` 的门必须在 `me` 检查**之前**路由 `/invite/accept` 与 `/password-reset/accept`(它们无会话工作):从 query 读 `token`,调 `GET …/accept` 校验,渲染设密码表单,POST,然后跳 Login。这是对外层鉴权门的唯一改动(在 `loading`/`anon` 分支里的一个小路径检查,或提升一个极小公开路由)。

### 7.4 自助账户 / 偏好页(`pages/Account.tsx`)
一个**自助**页,**所有角色**经用户菜单可达(区别于 admin 专属的 **Configuration** 页)。它承载用户对*自己*的全部管理项:
- **改密**(当前 + 新 + 确认)→ `POST /auth/change-password`,浮现 `auth.password_incorrect`(Step 10)。
- **每用户提醒提前期**(`reminder_best_before_lead_days` / `reminder_warranty_lead_days`,`PATCH /auth/me`)—— **从** Step 8 已门控为 `MANAGE_SETTINGS` 的 admin **Configuration** 页**迁入此处**(Step 10)。
- **每用户通知开关**(站内收件箱 + 邮件摘要)→ `PATCH /auth/me`(Step 12,§7.6)。

理由(M6 实施期间敲定的决策):Step 8 把 **Configuration** 改为 admin-only,故原本放在那里的**自助**每用户提醒偏好必须迁到非管理员也能到达的界面 —— 否则 member/viewer 会失去对自己提醒的控制。**Configuration** 仅保留**全局**提醒 + 通道配置(admin)。语言仍在头部 `LanguageSwitcher`。

### 7.5 责任人指派
定义表单(`DefinitionFormModal`)与实例表单(`InstanceFormModal`)上一个**用户选择器**(在 `GET /users` 上自动补全、可清空),外加详情页一行只读"责任人:…"。清空 = 继承(实例 → 定义)/ 未指派(定义)。

### 7.6 按用户通知偏好
自助 **Account** 页(§7.4,与每用户提醒提前期偏好并列)上两个开关 —— **站内收件箱**与**邮件摘要**,经 `PATCH /auth/me` 持久化。

### 7.7 审计日志页(`pages/Audit.tsx`,admin)
一张只读、分页表(时间、事件、操作者、目标、细节),带按 事件类型 / 操作者 / 日期范围 的过滤。

### 7.8 i18n
新命名空间:**`users`**、**`invitations`**、**`account`**(改密)、**`audit`**、**`roles`**(角色标签)、**`responsible`**(指派)(en + zh),在 `src/i18n/index.ts` 注册;`nav`(Users、Audit)与 `errors`(八个新码)新增。429 消息用 `params.retry_after_seconds`。测试钉在 `en`(M1.5)。

---

## 8. CI 与 Docker(相对 M5 的增量)

- **无新运行时依赖。** 限流、SSRF 守卫、token 哈希用标准库(`ipaddress`、`socket`、`hashlib`、`secrets`)+ 已有的 `argon2`/`httpx`。`AGENTS.md` 依赖行**不变**。
- **Docker**:**migrate** 冒烟步骤现应用 `0001`–`0032`。单镜像与单 bind mount 不变。新东西全部零配置:全新部署仍引导单个 admin(M0 setup),新行为只在存在第二个用户/角色/指派后才出现。
- **部署注意(反代后的限流):** 进程内限流器按 `request.client.host` 取键;若 Omniventory 跑在反代后,operator 应转发/信任 `X-Forwarded-For` 以获得准确的按客户端限流(有记录;完整代理信任配置见 §12)。
- 其余(契约门、缓存、fail-closed `migrate` 服务)与 M0 §8 / M5 §8 完全一致。

---

## 9. 步骤拆分(原子、有序)

每步独立可测、有测试、落正好一个提交(编排模式下一次 per-step autosquash),触碰 API 则重跑 `make codegen`,并继承全局 DoD(§5)+ **反夹带规则**:只实现当前步 —— 不做其它步、不夹带机会主义重构、不碰宿主真实环境(测试沙箱外不动真实 DB/容器/文件)。

> **分阶段:** A(角色与权限)→ B(用户账号与邀请)→ C(责任人路由与按用户偏好)→ D(审计 + 硬化)→ E(前端)。后端 A–D 稳定契约;前端 E 消费它。E 各步依赖各自后端对应步。

### 阶段 A — 角色与权限(后端)

**步骤 1 — 权限矩阵 + `require_permission` + 强制扫描**
- **构建:** `app/auth/permissions.py`(`Role`、`Permission`、`PERMISSIONS`、`has_permission`);`deps.py` 中 `require_permission(perm)` + 别名;角色取值校验(应用层);给**所有**现有数据写加 `require_permission(EDIT)`、给 设置/通道 + `POST /reminders/run` 加 `MANAGE_SETTINGS`;错误码 `auth.forbidden`;`make codegen`。
- **测试:** 矩阵真值表;每个门控路由按角色允许/拒绝;viewer 无法写任何东西;读对所有人开放;自助路由不门控。
- **提交:** `feat(backend): role-based permission matrix and enforcement`

### 阶段 B — 用户账号与邀请(后端)

**步骤 2 — 用户管理(列/角色/启停/删)+ 最后管理员保护**
- **构建:** `UserRepository` 扩展(列全部、设角色、设活跃、删、统计活跃 admin、可选列表);`UserAdminService` 带最后管理员保护;`GET /users`(`VIEW`)、`GET/PATCH/DELETE /users/{id}` + `UserSummary`/`UserAdminUpdate`;错误码 `user.not_found`、`user.last_admin`;`make codegen`。
- **测试:** 改角色;启停;删除;降级/停用/删除上的最后管理员保护;`GET /users` 对所有已登录开放;变更仅 admin。
- **提交:** `feat(backend): user administration with last-admin guard`

**步骤 3 — 邀请、重置密码与自助改密(`user_tokens`)**
- **构建:** 迁移 `0028`(`user_tokens`);模型 + `UserTokenRepository`;`InvitationService`(+ 重置变体)+ 自助改密;SMTP 发送辅助(复用 M4 传输);`POST/GET/DELETE /invitations`、`GET/POST /invitations/accept`、`POST /users/{id}/reset-password`、`GET/POST /password-reset/accept`、`POST /auth/change-password`;lifespan token 清理;错误码 `auth.invalid_token`、`auth.password_incorrect`、`user.email_exists`、`invitation.not_found`;`make codegen`。
- **测试:** 邀请 创建/重复/撤销先前;接受建用户;已用/过期/未知 token;token 落库哈希;重置 签发+接受;自助改密 当前错/对;其它会话撤销;迁移升降级。
- **提交:** `feat(backend): invitations, password reset, and change-password`

### 阶段 C — 责任人路由与按用户偏好(后端)

**步骤 4 — 定义 + 实例上的 `responsible_user_id`**
- **构建:** 迁移 `0029`(`item_definitions`)+ `0030`(`stock_instances`);模型字段(`SET NULL`);串进定义 + 实例的 create/update/response schema + 服务;`make codegen`。
- **测试:** 两实体上设/清;删用户时 FK `SET NULL`;往返;迁移升降级。
- **提交:** `feat(backend): responsible-party assignment on definitions and instances`

**步骤 5 — 提醒收件人路由 + 按用户通知偏好**
- **构建:** 迁移 `0031`(`users.notify_in_app` + `notify_email_digest`);引擎收件人解析(`effective_responsible` + 日期源&低库存的回退全体,scan + event 路径);建行偏好把守 + 收件箱把守 + 邮件摘要跳过;把两个偏好串进 `PATCH /auth/me` + `UserResponse`;`make codegen`。
- **测试:** 实例/定义/回退路由;停用/删除责任人 → 回退;低库存路由;邮件摘要退订;站内退订清空收件箱;两者皆关 → 无行;M4 对齐默认;迁移升降级。
- **提交:** `feat(backend): responsible-party reminder routing and per-user channel prefs`

### 阶段 D — 审计与安全硬化(后端)

**步骤 6 — 审计日志**
- **构建:** 迁移 `0032`(`audit_log`);模型 + `AuditLogRepository` + `AuditService`;把 `record(...)` 接入 认证/用户/邀请/密码/设置 代码路径(含 `auth.login_succeeded`/`login_failed`/`logout`);`GET /audit`(`VIEW_AUDIT`)带过滤 + 分页 + `AuditLogResponse`;`make codegen`。
- **测试:** 每事件写一正确行;失败登录行(NULL actor、email 快照);过滤 + 分页;admin 专属;迁移升降级。
- **提交:** `feat(backend): security and admin audit log`

**步骤 7 — 安全硬化(限流 + SSRF + media 授权 + 会话滑动窗口)**
- **构建:** `app/core/rate_limit.py`(`RateLimiter` + 指数退避)+ login/setup/change-password/token-accept 上的 `auth_rate_limit(scope)` 依赖;错误码 `auth.rate_limited`(+ `Retry-After`);`app/core/net_guard.py`(`validate_outbound_url`、`validate_broker_host`)接入 webhook(`follow_redirects=False`)+ MQTT host;`/media` 上 `Depends(get_current_user)`;`sessions.verify()` 滑动窗口刷新;`make codegen`(仅 `auth.rate_limited` 经错误响应进 schema)。
- **测试:** 限流 阈值/退避/封顶/清零/按键 + 429+Retry-After;SSRF 放行/拒绝集合含私网放行;webhook 不重定向;`/media` 无会话 401;滑动窗口 延长-vs-不写 + 已提交 + 过期仍 401。
- **提交:** `feat(backend): auth rate limiting, SSRF guard, media auth, sliding sessions`

### 阶段 E — 前端

**步骤 8 — 鉴权上下文 + 角色门控**
- **构建:** `AuthContext`(user/role/`can`)+ 客户端权限镜像;nav/路由门控;对 viewer 隐藏/禁用写操作;资料按钮上角色;`roles` 命名空间(en+zh)。
- **测试:** 按角色 nav/路由;viewer 写操作隐藏;admin 专属重定向。
- **提交:** `feat(frontend): auth context and role-based UI gating`

**步骤 9 — 用户管理页 + 邀请 UI**
- **构建:** `pages/Users.tsx`(列出、改角色、启停、删除带最后管理员错、重置密码)+ 邀请模态(复制链接 + 可选发邮件)+ 待定邀请列表/撤销;`users` + `invitations` 命名空间(en+zh)。
- **测试:** 列/角色/活跃/删;最后管理员浮现;邀请显示链接 + 发送;撤销。
- **提交:** `feat(frontend): users administration and invitations`

**步骤 10 — 公开接受页 + 改密**
- **构建:** `pages/AcceptInvite.tsx` + `pages/ResetPassword.tsx`(预登录,token 取自 query)+ `App.tsx` 门改动以匿名路由它们;一个自助 **`pages/Account.tsx`**(所有角色、经用户菜单进入)承载改密表单**以及从** admin Configuration 页**迁出的**每用户提醒提前期偏好(§7.4);`account` 命名空间(en+zh)。
- **测试:** 接受邀请设密码→登录;接受重置;无效 token 态;改密 当前错/对。
- **提交:** `feat(frontend): invite/reset accept pages and change-password`

**步骤 11 — 责任人指派 UI**
- **构建:** 定义 + 实例表单上的用户选择器 + 详情上只读显示;`responsible` 命名空间(en+zh)。
- **测试:** 两表单设/清;继承显示。
- **提交:** `feat(frontend): responsible-party assignment`

**步骤 12 — 按用户通知偏好 UI**
- **构建:** 自助 **Account** 页(§7.4/§7.6)站内 + 邮件摘要开关,经 `PATCH /auth/me` 持久化。
- **测试:** 开关往返;反映服务端态。
- **提交:** `feat(frontend): per-user notification preferences`

**步骤 13 — 审计日志页**
- **构建:** `pages/Audit.tsx`(admin)—— 分页表 + 过滤;`audit` 命名空间(en+zh)。
- **测试:** 列表渲染;过滤;分页;admin 门控。
- **提交:** `feat(frontend): audit log page`

> 步骤过大可拆(如步骤 1 矩阵-vs-扫描、步骤 3 邀请-vs-密码、步骤 7 按硬化关注点);各自保持独立绿。阶段内后端步基本独立;E 步依赖各自对应(8→1、9→2/3、10→3、11→4、12→5、13→6)。

---

## 10. 盲审检查点(每步)

审查者**只**拿到:本文 + roadmap、该步实现简报、该步 diff。检查:

- **步骤 1:** 矩阵精确匹配 §2;`require_permission` 抛 `auth.forbidden`/403;**每个**数据写被门控 `EDIT` 且**没有**读路由被过度门控;设置 + run-scan 是 `MANAGE_SETTINGS`;自助路由不门控;角色取值集应用层校验(无 DB CHECK);codegen 已提交。
- **步骤 2:** 最后管理员保护在 降级/停用/删除 上成立;`GET /users` 是 `VIEW`(选择器)而变更是 `MANAGE_USERS`;`user.not_found`/`user.last_admin`;codegen 已提交。
- **步骤 3:** token 落库**哈希**;已用/过期/未知 → `auth.invalid_token`;重复邮箱 → `user.email_exists`;新邀请撤销先前待定;接受用对的角色建用户;自助改密校验当前(`auth.password_incorrect`)并撤销*其它*会话;清理已接;codegen 已提交。
- **步骤 4:** `responsible_user_id` 在**两表**上可空 `SET NULL`;串进两 schema;非追溯;codegen 已提交。
- **步骤 5:** 解析 = 实例→定义→回退全体(日期源)与 定义→回退全体(低库存);停用/删除责任人 → 回退(不丢提醒);lead-time 链仍应用;偏好把守正确(行-当且仅当-任一通道、收件箱门、邮件跳过);M4 对齐默认;codegen 已提交。
- **步骤 6:** 只追加;失败登录行有 NULL actor + email 快照;每个被覆盖路径都记录;admin 专属列表带过滤+分页;codegen 已提交。
- **步骤 7:** 限流 阈值/指数翻倍/封顶/清零/按键 + 429+`Retry-After`;SSRF 拦 loopback/链路本地/元数据/保留但**放行**私网 LAN,`follow_redirects=False`;`/media` 无会话 401;滑动窗口仅在临近过期时延长**且提交**且绝不复活已过期会话;codegen 已提交。
- **步骤 8:** 门控镜像矩阵;后端仍是真相来源;en+zh 齐全。
- **步骤 9:** 用户表动作经类型化 client;最后管理员错浮现;邀请链接复制 + 可选邮件;撤销;en+zh 齐全。
- **步骤 10:** 接受页**预登录**工作(门改动正确);无效 token 态;改密流程;en+zh 齐全。
- **步骤 11:** 在 `GET /users` 上的选择器、可清、两表单;详情显示;en+zh 齐全。
- **步骤 12:** 两开关经 `PATCH /auth/me` 往返;en+zh 齐全。
- **步骤 13:** `/audit` 表 + 过滤 + 分页,admin 门控;en+zh 齐全。
- **横切:** 匹配 roadmap **§1.2(单租户、多用户;所有用户共享全部数据 —— 责任人是路由而非访问控制)**、§2.6(提醒路由到责任人、保留回退)、§2.10(单一上下文/仓储层)、§2.11(逻辑在应用层、角色无 DB CHECK/enum);M1.5 统一错误信封(仅八个新码,无裸 `detail`);本文变更时的**双语文档规则**。

---

## 11. 🟢 部署自测点(拼入 M6 里程碑走查)

作者在里程碑末手动走查(展开 roadmap M6 🟢 条目)。假定 M1–M5 运行流程(compose up 经 HTTPS 或 localhost;以 admin 登录;存在一些 位置/定义/库存)。

1. **邀请第二个用户:** 以 admin 打开 **Users → Invite**,输入邮箱 + 角色 `member` → 复制**接受链接**(若配置 SMTP,则发送)。在全新浏览器打开链接,**设密码**,落到登录,以新 member 登录。
2. **角色强制:** 以 **member**,确认能创建/编辑/消耗库存,但**看不到** Users / Configuration / Audit,直接访问这些 URL 也被拒。邀请/创建一个 **viewer**;确认 viewer **只读**看到一切(无 创建/编辑/删除/入库 控件)。
3. **最后管理员保护:** 尝试降级或停用**唯一** admin → 被拦带清晰消息;加第二个 admin 后即可。
4. **责任人路由:** 把某耐用品的**实例**(或其定义)指派给 member;设其 `warranty_expires` 临近 lead 窗口;**运行扫描** → 只有 **member** 收到该提醒。另留一物品**未指派** → 其提醒到**全体**。把某消耗品的**定义**指派给 viewer,跌破 `min_stock` → 只有 viewer 收到低库存提醒。
5. **按用户偏好:** 以 member,**关闭邮件摘要**(保留站内)→ 下次摘要跳过他但站内铃铛仍显示;**关闭站内** → 铃铛清空。
6. **密码:** 改自己密码(当前错 → 清晰错;对 → 成功,其它会话被登出);以 admin,经链接**重置** member 的密码。
7. **审计日志:** 以 admin 打开 **Audit** → 看到这些登录(含一次故意失败的)、邀请签发 + 接受、改角色、设置变更;按 事件类型 与 操作者 过滤。
8. **限流:** 用错误密码猛敲登录数次 → 响应**指数退避**(429 + "N 秒后再试");一次正确登录清掉它。
9. **SSRF + media:** 把 webhook URL 设为 `http://169.254.169.254/x` → 通知器拒绝它(LAN/公网 URL 被接受);在**已登出**标签页打开 `/media/...` URL → **401**;登录后 → 图片加载。
10. **滑动会话:** 持续活跃越过旧固定窗口 → 仍登录;闲置越过 TTL → 被登出。
11. **CI 绿:** 同样的门在 GitHub Actions 通过,含无漂移契约门;迁移 `0001`–`0032` 在 docker 冒烟里于全新库干净应用。

---

## 12. 开放问题 / 推迟

- **自定义角色 / 细粒度权限:** 三角色代码矩阵是既定粒度。`custom_role` + 按资源 ACL(DB 驱动)是有界后续,若真实使用需要。
- **按用户节奏 / 按通道路由:** M6 发两个退订(站内、邮件摘要)。完整"每用户 × 通道 × 计划"(及让 HTTP/MQTT 按用户)继续搁置(M4 §12)。
- **站内 vs 邮件解耦:** 站内**关**但邮件**开**的用户仍得到一个 `notifications` 行(邮件源),收件箱查询隐藏它 —— 一个刻意简化。更干净的拆分(每行单独的收件箱可见性标志,或一张 outbox 表)是改良项。
- **SSRF 更严模式:** M6 放行私网 LAN 并接受解析后再连接的 DNS-rebinding TOCTOU(admin 配置的 URL)。可选的"拦私网目标"开关与 IP 钉死可后续,若威胁模型变化。
- **限流存储与代理信任:** 进程内、单进程、重启重置、按 `request.client.host` 取键。持久化/共享存储与有记录的 `X-Forwarded-For` 信任配置在多进程或前置代理出现时再落。
- **OIDC / SSO / 2FA / 邮箱验证注册:** parking lot;M6 里邀请链接即信任路径。
- **审计范围与保留:** 仅安全/管理事件;无通用数据变更审计(账本已覆盖库存),尚无保留/轮转策略(表无界增长 —— 修剪作业是后续改良)。
- **会话控制:** 仅滑动窗口;显式"活跃会话"列表 + "处处登出" UI 与绝对寿命上限是改良项。
- **账户自助:** M6 有改密 + 偏好;自助改邮箱与账户删除待后续。
