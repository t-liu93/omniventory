# M0 — 地基与脚手架

> 🌐 **语言:** [English](./M0.md) · 中文(当前)

> **里程碑设计文档——自包含。** 配合 `docs/plan/roadmap.md`（总图）阅读；"我们为何存在"见 `docs/inspiration/investigation.md`。本文是 *M0 要造什么、如何验收* 的唯一真相，不要再从 roadmap 反推范围。进度**只**记录在 roadmap §4 的表里。
>
> 内建约定：原子步骤（§9）、盲审检查点（§10）、🟢 部署自测点（§11），手动与编排两种执行模式都挂在本文上。

---

## 1. 目标与非目标

**目标（roadmap 对 M0 的承诺）:** 一个 *空但能跑*、CI 全绿、能登录进去的应用。暂无任何领域功能——只有骨架，以及后续每个里程碑都依赖的质量闸。

**完成判据（🟢，§11 展开）:** 应用能启动；`GET /api/health` 绿；登录可用；一条 Alembic 迁移能干净地应用到空库；CI 全绿；`docker build` 产出一个能跑起整套的单镜像。

**明确不在 M0 内:**
- 除 `Household/Workspace` 单例外，**不**建任何领域模型——Item/Definition/Lot/Location/Category/Movement 全部从 M1 起（M0 不碰）。
- 不做多用户、角色、邀请（M6）。M0 只引导出**恰好一个** admin 用户。
- 不做提醒、不做邮件/SMTP、不做条码、不做 CSV/导入导出、不做 LLM 钩子。
- 除基线卫生外不做生产级加固（安全加固在 M6）。
- 不做定制视觉设计——前端只给**轻量主题地基 + 响应式壳**（§7）。

---

## 2. 锁定决策（规划阶段已定；理由见对话记录与 roadmap §1）

| 方面 | 决策 |
|---|---|
| 后端语言/工具链 | **Python 3.13**，由 **`uv`** 管理；lint/format 用 **ruff**，类型检查 **mypy**，测试 **pytest**。 |
| Web 框架 | **FastAPI** + **Pydantic v2**（配置用 `pydantic-settings`）。 |
| API 形态 | v1 **不带 URL 版本前缀**（`/api/...`）；所有路由挂在一个可配置的 `api_prefix` 下，并在 `/api/health` 暴露一个整数 **`api_version`** 供兼容性探测。仅当 M9 的公共 API 真要做破坏性变更时，才加 `/api/v2`。 |
| 持久化 | **SQLAlchemy 2.0**（带类型，`Mapped[...]`）+ **Alembic** 迁移 + **SQLite**（默认，文件落在 volume 上）。 |
| 前端语言/工具链 | **React + TypeScript** + **Vite**，包管理 **`pnpm`**；lint **eslint**，类型检查 **tsc**，测试 **vitest**（+ Testing Library）。 |
| UI | **Mantine** 组件库 + **`react-feather`** 图标；响应式（桌面与移动同等一等公民）；**PWA**（可安装、离线 app-shell）。 |
| 认证 | **Session-cookie**：不透明 session id 装进 `HttpOnly` + `Secure` + `SameSite=Lax` cookie；服务端 SQLite `sessions` 表。v1 不用 JWT。 |
| 契约 | **契约优先**：FastAPI OpenAPI → `openapi-typescript` → `frontend/src/api/schema.d.ts`；运行时调用走 **`openapi-fetch`**。**no-drift CI 闸**（§6）。 |
| 仓库形态 | **Monorepo**：本仓库，根下 `backend/` + `frontend/`。 |
| 部署 | **单 Docker 容器**：内嵌已构建前端 + FastAPI + SQLite volume。**生产 `docker-compose.yaml`**（用预构建 image，无 `build`）+ **`docker-compose.dev.yaml`** override（开发时加 `build`）。 |

> 按 `AGENTS.md`，这些属"foundations"；M0 落地后再填 `AGENTS.md` 的 "Tech stack & commands" 一节（且仅在那时）。

---

## 3. 仓库布局

```
omniventory/
├── backend/
│   ├── pyproject.toml            # uv 项目；依赖、ruff/mypy/pytest 配置
│   ├── uv.lock
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/             # 第一条迁移放这里
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI app factory + 路由挂载
│   │   ├── config.py             # pydantic-settings Settings
│   │   ├── db/
│   │   │   ├── base.py           # DeclarativeBase、engine、session 工厂
│   │   │   └── session.py        # get_db / unit-of-work 依赖
│   │   ├── core/
│   │   │   └── context.py        # "current context" 抽象（多租户对冲）
│   │   ├── repositories/         # 仓储层（杜绝散落的裸查询）
│   │   ├── services/             # 应用/服务层（业务逻辑在这里）
│   │   ├── models/               # SQLAlchemy 模型（Household、Session、User）
│   │   ├── schemas/              # Pydantic 请求/响应模型
│   │   ├── api/
│   │   │   ├── deps.py           # FastAPI 依赖（current_user、db、context）
│   │   │   └── routes/
│   │   │       ├── health.py
│   │   │       └── auth.py
│   │   └── auth/                 # 密码哈希、session 创建/校验/吊销
│   ├── scripts/
│   │   └── export_openapi.py     # 导出 openapi.json 供 codegen 闸用
│   └── tests/
├── frontend/
│   ├── package.json              # pnpm；脚本：dev/build/lint/typecheck/test/codegen
│   ├── pnpm-lock.yaml
│   ├── vite.config.ts            # + vite-plugin-pwa
│   ├── tsconfig.json
│   ├── index.html
│   ├── public/                   # PWA 图标、manifest 资源
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── theme.ts              # Mantine 主题 token（"风格地基"）
│       ├── api/
│       │   ├── schema.d.ts       # 生成物——勿手改
│       │   └── client.ts         # openapi-fetch 实例
│       ├── shell/                # AppShell：响应式导航（sidebar/header/drawer）
│       ├── components/           # 共享模式种子（Loading/Empty/Error/PageShell）
│       └── pages/
│           └── Login.tsx
├── openapi.json                  # 生成的契约快照（提交入库；被 drift 闸卡）
├── docker-compose.yaml           # 生产：app 用预构建 image（无 build）
├── docker-compose.dev.yaml       # 开发 override：加 build + 开发环境
├── docker/
│   └── Dockerfile                # 多阶段；单一最终镜像
├── .dockerignore
├── .github/workflows/ci.yml
├── Makefile                      # 或 justfile：薄任务别名（codegen、lint、test…）
├── docs/   review-notes/   AGENTS.md   CLAUDE.md(符号链接)   README.md
```

根级 `openapi.json` + `Makefile` 是前后端的接缝；其余两侧各自自包含。

---

## 4. 后端骨架

### 4.1 应用与配置
- **App factory** `create_app()`（`app/main.py`）：构建 FastAPI 实例，把**所有路由挂在一个可配置的 `settings.api_prefix`** 下（默认 `/api`）——这样以后加 `/api/v1`（或 `/v2`）前缀只是一行改动——接入中间件（session；dev 下按需 CORS）。把 import 期副作用挡在外面。
- **配置**走 `pydantic-settings`（`app/config.py`）：`api_prefix`（默认 `/api`）、`api_version`（兼容性编号，见 §4.2）、`database_url`（默认 `sqlite:///./data/omniventory.db`）、`secret_key`、`session_cookie_name`、`admin_bootstrap_*`、`environment`。从 env / `.env` 读。**代码里不放密钥。**

### 4.2 健康检查
- `GET {api_prefix}/health` → `{ status: "ok", version, api_version, db: "ok" }`。`db` 字段做一次 `SELECT 1`，使健康反映数据库可达性。**`api_version`** 是一个整数兼容性编号（InvenTree 式）——这是我们替代 URL 版本化的选择：客户端/工具靠它探测兼容性，而无需我们对 URL 做版本化。这是一个 🟢 检查点。

### 4.3 持久化与迁移
- **SQLAlchemy 2.0** 带类型模型，统一一个 `DeclarativeBase`。engine + `sessionmaker` 放 `app/db/base.py`。
- **Alembic** 对同一份 metadata 接线；`env.py` 从 Settings 读 `database_url`。**第一条迁移**建 `households`、`users`、`sessions`。
- **业务逻辑留在服务层**——不写 SQLite 专有 SQL、视图或触发器（保持数据库可换；roadmap 约束 §2.11/§2.1）。

### 4.4 数据访问 / context 层（多租户对冲——roadmap §1.2）
- 所有 DB 访问都走**仓储层**（`app/repositories/`）+ 一个单一的 **"current context"** 抽象（`app/core/context.py`）——绝不在路由处理器里散落裸查询。
- context 携带 *当前 household* + *当前 user*；今天它解析成那个单例 household 与已登录用户。这就是日后加 `household_id` 范围过滤时把改动收敛、而非重写的接缝。

### 4.5 `Household/Workspace` 单例
- 一行：`name`、`currency`、`timezone`、`settings`（JSON）。由第一条迁移（或首次启动引导）创建。M0 用一个小不变量守卫保证它始终单例。

### 4.6 Session-cookie 认证骨架
- **密码哈希**：`argon2`（经 `argon2-cffi` / passlib）——绝不存明文。
- **`sessions` 表**：`id`（不透明随机，如 `secrets.token_urlsafe`）、`user_id`、`created_at`、`expires_at`、`last_seen_at`。滑动或固定过期（M0 固定即可）。
- **端点**（`app/api/routes/auth.py`）：`POST /api/auth/login`（设 cookie）、`POST /api/auth/logout`（删服务端 session + 清 cookie）、`GET /api/auth/me`（当前用户；无/无效 session 返回 401）。
- **Cookie**：`HttpOnly`、`Secure`、`SameSite=Lax`；值 = 不透明 session id（不含用户数据）。吊销 = 删那一行。
- **Admin 引导**：首次启动从 env/CLI 创建恰好一个 admin（幂等）。M0 不做注册 UI。
- **依赖** `current_user`（`app/api/deps.py`）读 cookie、查并校验 session、产出用户（否则 401）。

---

## 5. 质量闸（前后端本地命令）

| 关注点 | 后端 | 前端 |
|---|---|---|
| Lint/format | `ruff check` / `ruff format --check` | `eslint` |
| 类型检查 | `mypy app` | `tsc --noEmit` |
| 测试 | `pytest` | `vitest run` |
| 构建 | （无——打包在 Docker 里） | `vite build` |

`Makefile` 里给薄别名（如 `make lint`、`make test`、`make codegen`、`make check`），人和 CI 调同一套。**完成定义**（按 `AGENTS.md`）：上述全绿 + 构建通过；易错逻辑配单测（M0 主要是 **session 创建/校验/过期/吊销** 与**单例不变量**）。

---

## 6. 契约优先 codegen 与 no-drift 闸

流程：
1. `backend/scripts/export_openapi.py` 导入 app，把 OpenAPI 文档写到仓库根 **`openapi.json`**。
2. `openapi-typescript openapi.json -o frontend/src/api/schema.d.ts` 重新生成**纯类型**声明。
3. `frontend/src/api/client.ts` 用 `schema.d.ts` 包一层 `openapi-fetch`，使每次调用的路径/参数/响应都带类型。
4. **`openapi.json` 与 `schema.d.ts` 都提交入库。**

**no-drift CI 闸：** CI 跑 `make codegen`（= 第 1–2 步），然后 `git diff --exit-code openapi.json frontend/src/api/schema.d.ts`。若后端 API 改了却没重新生成提交，diff 非空 → **CI 失败**。这正是作者要的"有 drift 就过不去"。

---

## 7. 前端骨架与风格地基（轻量）

### 7.1 工具链
- Vite + React + TS；`pnpm` 脚本：`dev`、`build`、`lint`、`typecheck`、`test`、`codegen`。（用 `pnpm <script>` 跑。）

### 7.2 Mantine 主题地基 —— "风格地基"决策（轻定制）
集中在 `src/theme.ts`，使日后换肤 = 改这一个对象：
- **primary color**：沉稳、"可信赖库存"气质的色——提案 **`teal`**（或 `indigo`）；一行可改。
- **字体 / 圆角 / 间距**：Mantine 默认（不跟库对着干）。
- **明暗模式：day-one**——跟随系统 + 手动切换，一开始就接进壳（现在零成本，事后补很烦）。
- 由 `MantineProvider` + 色彩方案管理在 app 根部包裹。

### 7.3 响应式 app shell
- Mantine **`AppShell`**：桌面 = 常驻 **sidebar（navbar）+ header**；移动 = 收成 **burger → `Drawer`**（底部 tab 栏是待定项，§12——M0 默认 drawer）。**一处**壳定义；后续每个页面都挂在里面。

### 7.4 PWA 壳
- `vite-plugin-pwa`：web manifest（名称、图标、主题色、`display: standalone`），service worker 预缓存 **app shell** 以实现可安装/离线。（离线 = 仅静态资源；数据/认证仍在线。）

### 7.5 共享模式种子（保持极小）
- `components/`：`PageShell`、`LoadingState`、`EmptyState`、`ErrorState`。够 M1+ 页面不必各自重造即可。**不要**在这里造组件库。

### 7.6 认证 UI
- `pages/Login.tsx` 经类型化 client POST 到 `/api/auth/login`；成功后路由进壳。路由守卫调 `/api/auth/me`；未认证 → 登录。header 带登出动作。

> 独立的双语 *design-system* 文档**推迟**（§12）到 UI 长起来再建；M0 的地基就放在这里。

---

## 8. CI 与 Docker

### 8.1 CI（`.github/workflows/ci.yml`）
作业（能并行就并行），全部必须绿：
- **backend**：装 uv（+缓存）、`ruff`、`mypy`、`pytest`、对临时 SQLite 跑 alembic upgrade（迁移干净应用）。
- **frontend**：装 pnpm（**+ pnpm store 缓存**）与 node、`eslint`、`tsc`、`vitest`、`vite build`。
- **contract**：`make codegen` + `git diff --exit-code`（no-drift 闸）。
- **docker**：`docker build` 单镜像（冒烟：容器起得来、`/api/health` 绿）。

> CI 分钟数要省（免费档有上限）：**缓存 pnpm store 与 uv 缓存**，让重复运行时安装步骤近乎瞬时。

### 8.2 Docker（`docker/Dockerfile`，多阶段 → 单镜像）
1. **阶段 1（前端）**：`pnpm install --frozen-lockfile` + `pnpm build` → 静态资源。
2. **阶段 2（后端）**：`uv sync --frozen` 装进精简 Python 基础镜像。
3. **最终**：拷入后端 + 已构建前端；FastAPI 在 `/api/*` 提供 API，其余路径提供静态 SPA；SQLite 落在挂载 **volume**；启动时跑 alembic upgrade；`HEALTHCHECK` 打 `/api/health`。单容器、单端口。

### 8.3 Compose（生产 + 开发 override）
两个根级 compose 文件，用 Compose 的 override 机制叠加：
- **`docker-compose.yaml`（生产）**：`app` 服务从**预构建 `image:`** 运行（无 `build:`），SQLite 落命名 volume，env 来自 `.env`，端口映射、`restart`、healthcheck。
- **`docker-compose.dev.yaml`（开发 override）**：覆盖 `app`，加 **`build:`**（context `.`、`docker/Dockerfile`）+ 开发专用 env；设计成叠在生产文件之上（日后可加更多开发专用 override——挂载、端口等）。
- **开发启动命令：**
  ```bash
  docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build
  ```
  生产只用生产文件单独跑（用预构建 image，不 build）。

---

## 9. 步骤拆分（原子、有序）

每步可独立测试、有测试托底、恰好落一个 commit（编排模式下每步一次 per-step autosquash）。步骤按**盲编排**写——一个全新实现者应能仅凭本节 + 本文就把该步做出来。每步都继承全局完成定义（§5）与**防越界规则**：*只*做当前步——不做其他步、不顺手重构、不碰宿主机真实环境。

### 步骤 1 — Monorepo 脚手架、工具链与 CI 骨架
- **目标:** 仓库按 §3 铺好，两套工具链装好，所有闸在 trivial 样例上全绿。
- **构建:**
  - 根：`Makefile`（`check`/`lint`/`test`/`codegen` 别名；`codegen` 在步骤 5 前可为占位）、`.gitignore`、`.dockerignore`、`.github/workflows/ci.yml`。
  - `backend/`：`pyproject.toml`（uv 项目；运行时依赖 `fastapi`、`uvicorn[standard]`、`sqlalchemy`、`alembic`、`pydantic-settings`、`argon2-cffi`；开发依赖 `ruff`、`mypy`、`pytest`、`httpx`），锁定 **Python 3.13**，ruff + mypy + pytest 配置；提交 `uv.lock`；一个 smoke 测试。
  - `frontend/`：`package.json`（pnpm；依赖 `react`、`react-dom`、`@mantine/core`、`@mantine/hooks`、`react-feather`、`openapi-fetch`；开发依赖 `vite`、`typescript`、`eslint` + 配置、`vitest`、`@testing-library/react`、`vite-plugin-pwa`、`openapi-typescript`），脚本 `dev/build/lint/typecheck/test/codegen`；`tsconfig.json`、`vite.config.ts`、eslint + vitest 配置；提交 `pnpm-lock.yaml`；一个 smoke 测试。
  - `ci.yml`：一个 **backend** 作业（装 uv + 缓存 → ruff、mypy、pytest）和一个 **frontend** 作业（装 pnpm **+ store 缓存** + node → eslint、tsc、vitest、`vite build`）。docker + contract 作业在各自步骤里加。
- **测试:** 两个 smoke 测试通过；两侧 lint + 类型检查干净。
- **完成判据:** push → 空骨架上 CI 全绿。
- **不该做:** 无 app 代码、模型、路由、UI。
- **Commit:** `chore: scaffold monorepo, toolchains, and CI`

### 步骤 2 — 后端 app 核心（settings、app factory、health）
- **目标:** FastAPI 启动并在可配置前缀下提供 health（暂无 DB）。
- **构建:**
  - `app/config.py`：`Settings`，含 `api_prefix`（默认 `/api`）、`api_version`、`environment`、`secret_key`（必填）、`database_url`、`session_cookie_name`、`admin_bootstrap_email`/`admin_bootstrap_password`。
  - `app/main.py`：`create_app()` → FastAPI 实例，把一个根 `APIRouter` 挂在 `settings.api_prefix` 下；无 import 期副作用。
  - `app/api/routes/health.py`：`GET {api_prefix}/health` → `{status, version, api_version}`（`db` 字段步骤 3 加）。
- **测试:** `TestClient`——health 200 + 载荷形状；配置从 env 加载（含缺密钥时失败）。
- **完成判据:** 经 factory 启动；health 绿；闸绿。
- **不该做:** 无 DB、无认证。
- **Commit:** `feat(backend): app factory, settings, and health endpoint`

### 步骤 3 — 持久化、迁移与数据访问/context 层
- **目标:** 接好 SQLAlchemy + Alembic；第一条迁移建 `households` 单例；一切访问经仓储 + current-context 抽象。
- **构建:**
  - `app/db/base.py`（`DeclarativeBase`、engine、`sessionmaker`）、`app/db/session.py`（`get_db` unit-of-work 依赖）。
  - `app/models/household.py`：`Household(id, name, currency, timezone, settings JSON)` + **单例不变量**（应用层守卫 *和* DB 层守卫——如定值唯一列 / 单一允许 PK）。
  - `app/core/context.py`：`RequestContext`（当前 household；当前 user 步骤 4 加）+ 解析单例的 `get_context` 依赖。
  - `app/repositories/household.py`：`HouseholdRepository`（`get`/`ensure` 单例）——**路由里无裸查询**。
  - Alembic：`env.py` 读 `settings.database_url` + 目标 metadata；迁移 `0001` 建 `households` 并确保单例行。
  - health 经 `SELECT 1` 加上 `db: "ok"`。
- **测试:** 对全新临时 SQLite `alembic upgrade head`；第二个 household 插入被拒；仓储 `get`/`ensure`；health `db:ok`。
- **完成判据:** 迁移在空库上干净应用；闸绿。
- **不该做:** 无 `User`/`Session`（步骤 4），无领域表。
- **Commit:** `feat(backend): SQLAlchemy, Alembic, Household singleton, and context layer`

### 步骤 4 — Session-cookie 认证骨架
- **目标:** login / logout / me，配服务端 session + 一个引导出的 admin。
- **构建:**
  - `app/models/user.py`（`id, email, password_hash, role, is_active, created_at`）、`app/models/session.py`（`id` 不透明随机、`user_id`、`created_at`、`expires_at`、`last_seen_at`）；迁移 `0002`。
  - `app/auth/passwords.py`（argon2 哈希/校验）、`app/auth/sessions.py`（创建/校验/吊销/清过期）。
  - `app/api/routes/auth.py`：`POST {prefix}/auth/login`（校验 → 建 session → 设 `HttpOnly`+`Secure`+`SameSite=Lax` cookie）、`POST {prefix}/auth/logout`（吊销 + 清 cookie）、`GET {prefix}/auth/me`（当前用户 / 401）。
  - `app/api/deps.py`：`current_user`（cookie → 校验 session → 用户 / 401）；`RequestContext` 此时携带用户。
  - **Admin 引导**：从 `admin_bootstrap_*` 在启动时幂等创建（或 `make bootstrap` / CLI）。
- **测试（易错逻辑——必测）:** 密码哈希/校验；session 创建→校验往返；**过期 session 被拒**；logout 真正吊销（之后校验失败）；cookie 标志齐全；`me` 无 cookie 401 / 有 cookie 200；引导幂等（跑两次 = 一个 admin）。
- **完成判据:** login 设 cookie、`me` 可用、logout 吊销；闸绿。
- **不该做:** 无注册 UI、除"是合法用户"外无角色强制、无多用户。
- **Commit:** `feat(backend): session-cookie auth skeleton and admin bootstrap`

### 步骤 5 — 契约优先 codegen 与 no-drift CI 闸
- **目标:** 从实时 OpenAPI 生成 TS 类型；CI 在 drift 时失败。
- **构建:**
  - `backend/scripts/export_openapi.py`：导入 app，以**稳定键序**写仓库根 `openapi.json`（确定性 diff）。
  - `Makefile` `codegen`：导出 `openapi.json` → `openapi-typescript openapi.json -o frontend/src/api/schema.d.ts`。
  - `frontend/src/api/client.ts`：`openapi-fetch` `createClient<paths>({ baseUrl: '/api', credentials: 'include' })`。
  - 提交生成的 `openapi.json` + `frontend/src/api/schema.d.ts`。
  - CI **contract** 作业：`make codegen` 然后 `git diff --exit-code openapi.json frontend/src/api/schema.d.ts`。
- **测试:** 干净树上 `make codegen` 为 no-op（diff 空）；临时改一个端点确认闸变红，再回滚——**不留痕**。
- **完成判据:** 干净树上闸绿，drift 时可见地变红。
- **不该做:** 不生成运行时 client SDK（仅类型）。
- **Commit:** `build: contract-first OpenAPI→TS codegen with no-drift CI gate`

### 步骤 6 — 前端壳、主题与登录
- **目标:** 响应式 Mantine 壳 + PWA + 经类型化 client 可用的登录。
- **构建:**
  - `src/theme.ts`：Mantine 主题（primary `teal`、默认值）；`MantineProvider` + 色彩方案（系统默认 + 手动切换）在 app 根部。
  - `src/shell/AppShell.tsx`：桌面 sidebar + header；移动 burger → `Drawer`。**唯一**一个壳。
  - PWA：`vite-plugin-pwa` + web manifest + `public/` 图标；预缓存 app shell。
  - `src/components/`：极小的 `PageShell`、`LoadingState`、`EmptyState`、`ErrorState`。
  - `src/pages/Login.tsx`：表单 → `client.POST('/auth/login')` → 路由进壳；路由守卫经 `client.GET('/auth/me')`（未认证 → 登录）；header 里登出动作。
- **测试（vitest + Testing Library）:** Login 渲染 + 提交（mock client）；未认证时守卫重定向；色彩方案切换渲染。
- **完成判据:** `pnpm dev` 启动；登录与运行中的后端往返；PWA 可安装；桌面 + 移动响应式；闸绿。
- **不该做:** 无领域页面、除占位外无导航项、无组件库。
- **Commit:** `feat(frontend): Mantine shell, theme, PWA, and login`

### 步骤 7 — 单容器 Docker + 生产/开发 compose
- **目标:** 一个镜像跑起整套；生产 + 开发 compose，配 override 工作流。
- **构建:**
  - `docker/Dockerfile`：多阶段——(1) `pnpm install --frozen-lockfile` + `pnpm build`；(2) `uv sync --frozen`；(3) 精简最终镜像拷入后端 + 已构建静态；FastAPI 提供 `/api/*` + 带 **history 回退**的 SPA；启动跑 `alembic upgrade head`；`HEALTHCHECK` → `/api/health`；单端口。
  - `docker-compose.yaml`（**生产**）：`app` 从**预构建 `image:`**（无 `build:`）、SQLite 命名 volume、env 来自 `.env`、端口、`restart`、healthcheck。
  - `docker-compose.dev.yaml`（**开发 override**）：覆盖 `app` 加 **`build:`**（context `.`、`docker/Dockerfile`）+ 开发 env；叠在生产文件之上。
  - 记录开发命令：`docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build`。
  - CI **docker** 作业：构建镜像 + 冒烟（`docker run` → curl `/api/health`）。
- **测试:** 镜像构建；容器启动；迁移应用到全新 volume；`/api/health` 绿；浏览器可达登录。
- **完成判据:** 生产构建与开发 override 都能跑；CI docker 作业绿。
- **不该做:** 无 k8s/编排、无反向代理/TLS、无 Postgres。
- **Commit:** `build: single-container Dockerfile and prod/dev compose`

> **排序理由:** codegen（5）需要真实端点（2、4）来生成；前端（6）需要类型化 client（5）；Docker/compose（7）打包完成的整体。

---

## 10. 盲审检查点（逐步）

审查者**只**拿到：本文 + roadmap、该步实现简报、该步 diff。检查：
- **步骤 1**：无杂散业务代码；CI 真的跑全部闸 + 缓存；lockfile 已提交。
- **步骤 2**：app-factory 模式（无 import 期副作用）；配置来自 env，**无硬编码密钥**。
- **步骤 3**：**SQL 里无业务逻辑**（视图/触发器）；一切访问经仓储/context——路由里无裸查询；迁移可逆且干净应用；单例不变量在**应用层 + （合适时）DB 约束**双重保证。
- **步骤 4**：密码哈希（绝无明文）；cookie 为 `HttpOnly`+`Secure`+`SameSite`；session id 不透明且随机；logout 真正删服务端；过期被遵守；引导幂等。
- **步骤 5**：`schema.d.ts` + `openapi.json` 已生成并提交；闸确实在 drift 时失败；生成文件无手改。
- **步骤 6**：主题 token 集中在 `theme.ts`；**唯一**一个壳；明暗可用；桌面/移动断点都响应式；`localStorage` 里无密钥/令牌（认证基于 cookie）。
- **步骤 7**：单镜像；前端由后端托管；SQLite 落 volume（而非烤进镜像层）；healthcheck 接好。
- **横切**：符合 roadmap 全局约束 §2（尤其 §2.1、§2.10、§2.11）；本文落地时遵守双语文档规则。

---

## 11. 🟢 部署自测点（拼进 M0 里程碑走查）

里程碑末作者手动走查（每条展开一个 roadmap 🟢 要点）：

1. **构建镜像**：`docker build -f docker/Dockerfile .` 成功。
2. **运行它**：带 volume + admin 引导 env 的 `docker run`；容器启动，把 Alembic 迁移干净地应用到全新空库、无错。
3. **健康**：`curl /api/health` → `{status:"ok", db:"ok", version:...}`。
4. **登录可用**：浏览器打开应用 → 跳登录 → 用引导出的 admin 登入 → 进入响应式壳；cookie 为 `HttpOnly`（JS 看不到）；刷新仍登录；登出回到登录页。
5. **响应式 + PWA**：壳在桌面与移动宽度下都正常；明暗可切；应用可安装（PWA）且壳能离线加载。
6. **CI 绿**：同一套闸在 GitHub Actions 里通过，含 **no-drift** 契约闸。

---

## 12. 待定 / 推迟项

- **移动端导航形态**：`Drawer`（M0 默认）vs 底部 tab 栏——等有真实顶层栏目（约 M1–M2）再定。
- **独立 design-system 文档**：仅当 UI 面足够大时，再从 §7 拆出双语 `docs/plan/design-system.md`。
- **session 过期策略**：固定 vs 滑动窗口，以及"记住我"——M0 取固定；随 M6 多用户再议。
- **`react-feather` vs `@tabler/icons-react`**：roadmap 锁 `react-feather`；步骤 6 确认其覆盖度，有缺口则标记。
- **静态托管细节**：SPA 回退路由（history 模式）在后端静态挂载里处理——步骤 7 确认。
