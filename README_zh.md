# Omniventory

> 🌐 **语言:** [English](./README.md) · 中文(当前)

**一个自托管的"三合一"库存系统。** Omniventory 把三类通常各自为政的需求，统一进同一套数据模型：

1. **保质期 / 有效期** —— 食品、药品、易耗品，带主动的"提前 N 天"提醒。
2. **耐用品台账** —— 序列号、保修、价值、多级位置层级、照片，以及完整的生命周期跟踪。
3. **易耗品库存** —— 出入库流水台账、最低库存阈值、低库存提醒。

它是**单租户、多用户**（一次部署 = 一个家庭或团队），以**单个 Docker 镜像**交付，全部数据都放在**一个可直接拷贝备份的目录**里。个人自用优先，开源。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

> **状态：** 早期 / 1.0 之前 —— 首个打标签的版本是 **0.1.0**。核心已达到可日常使用的完成度；AI 辅助功能与外部集成在[路线图](./docs/plan/roadmap_zh.md)上。

## 功能

### 三合一核心

- **🥫 保质期与有效期** —— 批次级的保质日期，入库时按每类物品的默认保质天数**自动计算**；"即将过期" / "已过期"列表与仪表盘卡片。
- **📦 耐用品台账** —— 物品**定义**与单件**批次/实例**分离；序列号、型号、厂商、**保修**（一等公民级的提醒来源）、购入价 / 价值；自引用的**位置树**，其中一个容器本身也可以是被跟踪的物品；照片与生命周期。
- **🧴 易耗品库存** —— 只追加的**流水台账**（入库 / 消耗 / 移动 / 调整 / 报废）；数量**永远由台账推导、绝不被覆盖**；FEFO/FIFO 消耗；最低库存阈值、低库存提醒与撤销。

### 主动提醒

- **一套统一引擎**，覆盖保质期、保修、低库存、维护到期。
- **可配置提前期** —— 全局 **+ 按物品 + 按用户**。
- **事件触发 + 每日定时扫描**（双重保险），默认开启。
- **可插拔渠道** —— 站内收件箱、邮件（SMTP 摘要），以及出站 **HTTP webhook**（含一个 **Home Assistant** 状态端点）。*（MQTT / Home Assistant 桥接已实现，但当前禁用。）*

### 日常使用体验

- **多用户与角色** —— 管理员 / 成员 / 只读，邀请、责任人路由、审计日志，以及安全加固（认证限流、一个仍放行局域网的 SSRF 防护、会话管理）。
- **横切能力** —— 附件/照片、标签、备注、自定义字段、全局搜索。
- **条码扫描** —— 客户端 1D + 2D 解码，入库时做商品查找。
- **购物清单** —— 由低库存自动生成 + 手动条目；勾选某项可直接入库。
- **维护计划** —— 耐用品的周期性保养，接入提醒引擎。
- **数据可迁移** —— CSV 导出；数据都在一个绑定挂载的目录里（拷贝即备份）。

### 平台

- **双语界面** —— English + 简体中文，运行时可切换，并按账号记住。
- **可安装的 PWA** —— 桌面与移动端同等一流；带离线外壳。
- **大模型基座** —— 一个可配置、可连接测试的 OpenAI 兼容 provider；AI 辅助录入与语义检索在路线图上。

## 技术栈

- **后端** —— Python 3.13 · FastAPI · SQLAlchemy 2.0（带类型）· Alembic · SQLite · [uv](https://docs.astral.sh/uv/)。认证使用服务端不透明会话 cookie；全部业务逻辑在应用/服务层。
- **前端** —— React 19 + TypeScript · Vite · [Mantine](https://mantine.dev/) · react-i18next（ZH + EN）· PWA。pnpm。
- **契约优先** —— FastAPI 应用导出 `openapi.json`，据此生成带类型的 API 客户端；一个 no-drift 的 CI 关卡保证两者同步。
- **部署** —— 单个多阶段 Docker 镜像，内嵌并托管 SPA；SQLite 放在绑定挂载上。

## 快速开始（Docker）

前置：Docker + Docker Compose。

```bash
git clone git@github.com:omniventory/omniventory.git
cd omniventory

# 从 GHCR 拉取预构建的多架构镜像，先跑一次性的 migrate 服务
# （alembic upgrade head），成功之后 app 才启动（fail-closed）。
docker compose up -d
```

在 `.env` 里设 `IMAGE_TAG=0.1.0` 可锁定到某个发布版本（默认 `latest`）。想从源码构建而非拉镜像，用 `make docker-dev`。

然后在浏览器打开应用，完成**首次安装引导**（创建管理员账号）—— 不存在由环境变量预置的管理员。

数据存放在绑定挂载的 `DATA_DIR`（在容器内映射到 `/app/data`：SQLite 文件 + 上传的媒体）。容器以 **uid/gid 1000** 运行。**备份方式：停掉容器，拷贝那个目录即可。**

### 配置

默认零配置。可通过 `docker-compose.yml` 旁边的可选 `.env` 覆盖：

| 变量 | 用途 |
| --- | --- |
| `IMAGE_TAG` | 要运行的已发布镜像标签（`ghcr.io/omniventory/omniventory:<IMAGE_TAG>`）；默认 `latest`。 |
| `APP_PORT` | 对外暴露应用的主机端口。 |
| `DATA_DIR` | 绑定挂载到 `/app/data` 的主机路径（SQLite + 媒体）。 |
| `SECRET_KEY` | 会话签名密钥。留空则首次运行时自动生成并持久化。 |
| `DATABASE_URL` | 数据库连接串（默认指向 `/app/data` 下的 SQLite 文件）。 |
| `ENVIRONMENT` | `production` / `development`。 |

当把 Omniventory 暴露到局域网之外时，建议在前面套一层反向代理（Nginx、Caddy……）来做 TLS；容器只暴露一个 HTTP 端口。

## 开发

前置：Python 3.13、[uv](https://docs.astral.sh/uv/)、Node 22、pnpm、Docker。

```bash
# 后端（在 backend/ 下）
uv sync
uv run uvicorn app.main:create_app --factory --reload

# 前端（在 frontend/ 下）
pnpm install
pnpm dev
```

仓库根目录的 `Makefile` 目标是规范入口 —— 人和 CI 调用同一套：

- `make check` —— 所有质量关卡（两端的 lint + 类型检查 + 测试）。这是 Definition-of-Done 关卡。
- `make lint` · `make test` —— 上面的两半。
- `make codegen` —— 重新生成 `openapi.json` + 前端 API 类型。API 一变就重跑并提交（有 CI 关卡在漂移时失败）。
- `make docker-build` —— 从源码构建一个本地镜像（`omniventory:latest`）。
- `make docker-dev` —— 从源码构建 + 运行 dev 栈（打成 `omniventory:dev`，绝不会遮蔽已发布的 GHCR 镜像）。

## 项目状态与路线图

Omniventory 按里程碑逐步构建。核心（统一模型、库存台账、有效期、提醒引擎、横切能力 + 条码 + 导出、多用户与角色、购物清单 + 维护）已完成；多单位换算暂停；大模型应用与外部集成为计划中。里程碑地图与进度表见 **[`docs/plan/roadmap_zh.md`](./docs/plan/roadmap_zh.md)**。

`docs/` 下的设计文档都是双语的（英文正本 `<name>.md` + 中文镜像 `<name>_zh.md`）。

## 贡献

本项目起于个人自用，开源出来供他人自托管与二次开发。欢迎 issue 与 PR。提交 PR 前请跑 `make check`，并用 `make codegen` 保持 OpenAPI 契约同步。

## 许可

[MIT](./LICENSE) © 2026 Omniventory
