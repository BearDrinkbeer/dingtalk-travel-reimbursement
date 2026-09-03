# 技术栈升级审计

审计日期：2026-09-02（Asia/Shanghai）  
版本口径：只统计当日的稳定版，不把 alpha、beta、RC、dev 版本作为升级目标。  
资料口径：版本号取自 npm、PyPI、Docker Hub、GitHub Releases 等官方发布元数据；兼容性取自项目官方文档。

## 结论

项目的核心技术路线不需要更换。Vue 3 + Vite + FastAPI + SQLite + PaddleOCR + openpyxl 对这个轻量内部工具仍然合适。还有升级空间，但应分成三类处理：

- **可直接升级**：Vue Router 5、eslint-plugin-vue、typescript-eslint、globals、Vue Test Utils 等；它们不改变应用架构，跑完既有检查即可。
- **需迁移验证**：Node 24 + jsdom 30、ESLint 10、Python 3.13、Uvicorn、Pydantic、SQLAlchemy/Alembic、Ruff、uv、Nginx。它们涉及运行时、行为或工具规则变化，应分批升级。
- **暂缓**：Node 26（仍是 Current，不是 LTS）、Python 3.14（PaddlePaddle 没有 CPython 3.14 wheel）、Nginx 1.31 mainline、SQLAlchemy 2.1 RC、HTTPX 1.0 dev，以及无实际需求时新增 PyMuPDF。

## 本轮实施结果

审计后已落地以下新基线：

- 前端：Vue Router 5.3.0、ESLint 10.9.1、`@eslint/js` 10.0.1、eslint-plugin-vue 10.10.0、typescript-eslint 8.69.0、Vue Test Utils 2.5.0、globals 17.12.0、jsdom 30.0.1、`@types/node` 24.13.3；构建镜像固定为 Node 24.20.0 LTS。
- 后端：Alembic 1.19.1、Pydantic 2.13.5、pydantic-settings 2.15.0、SQLAlchemy 2.0.52、Uvicorn 0.52.4、Ruff 0.16.5；运行镜像固定为 Python 3.13.15 slim-trixie，uv 镜像改为 0.12.9。
- 测试客户端：业务和 PaddlePaddle 继续使用 HTTPX 0.28.1；开发依赖新增 HTTPX2 2.12.0，供 Starlette `TestClient` 使用，消除其 HTTPX 兼容路径弃用警告。
- 部署入口：Nginx 改为 1.30.4 stable。

已通过 259 项后端测试、50 项前端测试、TypeScript 7/6 双检查、ESLint 10、Ruff、Vite 生产构建、Compose 配置、Nginx 策略、OCR 模型固定 SHA 校验，以及 PaddleOCR 3.7/PaddlePaddle 3.3.1 在 Python 3.13 上的本地 CPU 初始化和最小推理。Uvicorn 0.52 也已完成真实进程启动、健康/就绪检查和优雅关闭。Docker daemon 当时未启动，因此新基础镜像的完整 Compose 构建仍需在 Docker 可用时补跑。

审计开始时的可追溯基线是 Git 提交 `c9335c2`。下文“基线”均指该提交中的版本；这样即使后续实施升级，表格仍能说明版本变化的起点。

## 前端

### 核心依赖

| 组件 | 基线 | 2026-09-02 最新稳定版 | 兼容性与项目判断 | 分类 |
|---|---:|---:|---|---|
| [Vue](https://registry.npmjs.org/vue/latest) | 3.5.42 | **3.5.42** | 已是最新；`@vue/compiler-sfc` 应继续与 Vue 保持完全相同版本。 | 无需升级 |
| [Vue Router](https://registry.npmjs.org/vue-router/latest) | 4.5.1 | **5.3.0** | v5 要求 Vue `^3.5.34`、Vite 7/8；项目均满足。官方说明：未使用文件路由插件的 Vue Router 4 项目升级到 v5 没有破坏性代码改动。本项目使用普通 `createRouter`，未使用 `unplugin-vue-router`。[官方迁移说明](https://router.vuejs.org/guide/migration/v4-to-v5) | **可直接升级**，验证导航守卫和刷新回退 |
| [Axios](https://registry.npmjs.org/axios/latest) | 1.20.0 | **1.20.0** | 已是最新；当前 Cookie、CSRF、Blob 下载流程无须改用别的 HTTP 客户端。 | 无需升级 |
| [Element Plus](https://registry.npmjs.org/element-plus/latest) | 2.14.5 | **2.14.5** | 已是最新，Vue peer 要求 `^3.3.7`，当前满足。 | 无需升级 |
| [ESLint](https://registry.npmjs.org/eslint/latest) | 9.28.0 | **10.9.1** | v10 要求 Node `^20.19 || ^22.13 || >=24`；项目已经使用 flat config，但推荐规则、新配置查找和部分规则行为发生变化。[官方 v10 迁移指南](https://eslint.org/docs/latest/use/migrate-to-10.0.0) | **需迁移验证** |
| [@eslint/js](https://registry.npmjs.org/%40eslint%2Fjs/latest) | 9.28.0 | **10.0.1** | peer 要求 ESLint `^10`，必须和 ESLint 10 一起升级。 | **需迁移验证** |
| [eslint-plugin-vue](https://registry.npmjs.org/eslint-plugin-vue/latest) | 10.1.0 | **10.10.0** | 支持 ESLint 8.57/9/10 和 typescript-eslint parser 7/8；可以先在 ESLint 9 下升级，也可以纳入 ESLint 10 批次。 | **可直接升级** |
| [@types/node](https://registry.npmjs.org/%40types%2Fnode/latest) | 22.15.29 | **26.4.1**（全局 latest） | 类型定义应匹配实际构建运行时，而不是机械采用全局 latest。若迁移到 Node 24，目标应是 **24.13.3**；若继续 Node 22，可先升到 22.20.1。 | 与 Node 一起 **需迁移验证** |
| [jsdom](https://registry.npmjs.org/jsdom/latest) | 26.1.0 | **30.0.1** | v30 要求 Node `^22.22.2 || ^24.15 || >=26`。当前浮动 `node:22-alpine` 不清楚具体补丁，推荐先切到 Node 24 LTS，再升级 jsdom 并跑完整组件测试。 | **需迁移验证** |
| [unplugin-auto-import](https://registry.npmjs.org/unplugin-auto-import/latest) | 21.1.0 | **21.1.0** | 已是最新，要求 Node `>=20.19`。 | 无需升级 |
| [unplugin-vue-components](https://registry.npmjs.org/unplugin-vue-components/latest) | 32.1.0 | **32.1.0** | 已是最新，要求 Node `>=20.19`，Vue peer 为 `^3`。 | 无需升级 |
| [dingtalk-jsapi](https://registry.npmjs.org/dingtalk-jsapi/latest) | 3.2.9 | **3.2.9** | npm `latest` 仍为 3.2.9。免登依赖钉钉客户端行为，不应换成非官方封装。 | 无需升级 |

### 同一批需要关注的前端工具

| 组件 | 基线 | 最新稳定版 | 建议 |
|---|---:|---:|---|
| [Pinia](https://registry.npmjs.org/pinia/latest) | 4.0.3 | **4.0.3** | 已是最新。 |
| [Vite](https://registry.npmjs.org/vite/latest) | 8.2.2 | **8.2.2** | 已是最新。官方要求 Node 20.19+ 或 22.12+；推荐用 Node 24 LTS构建。[Vite 官方兼容说明](https://vite.dev/guide/) |
| [@vitejs/plugin-vue](https://registry.npmjs.org/%40vitejs%2Fplugin-vue/latest) | 6.0.8 | **6.0.8** | 已是最新，兼容 Vite 5–8。 |
| [Vitest](https://registry.npmjs.org/vitest/latest) | 4.1.11 | **4.1.11** | 已是最新；升级 jsdom 后重新运行全部 50 个前端测试。 |
| [vue-tsc](https://registry.npmjs.org/vue-tsc/latest) | 3.3.11 | **3.3.11** | 已是最新。 |
| [typescript-eslint](https://registry.npmjs.org/typescript-eslint/latest) | 8.68.0 | **8.69.0** | 可直接升级。它声明 TypeScript `<6.1`，所以当前“TypeScript 7 原生检查 + TypeScript 6 供 Vue/ESLint 工具使用”的双版本桥接仍应保留。 |
| [globals](https://registry.npmjs.org/globals/latest) | 16.2.0 | **17.12.0** | 数据包升级；可能使 lint 结果变化，跑 `npm run lint` 即可。 |
| [@vue/test-utils](https://registry.npmjs.org/%40vue%2Ftest-utils/latest) | 2.4.6 | **2.5.0** | 同一 Vue 3 主版本，可直接升级并运行组件测试。 |
| [dingtalk-h5-remote-debug](https://registry.npmjs.org/dingtalk-h5-remote-debug/latest) | 0.1.3 | **0.1.3** | 已是最新；仅开发调试按需加载，继续避免进入生产路径。 |

### 前端推荐批次

1. 先升级 Vue Router 5、eslint-plugin-vue、typescript-eslint、globals、Vue Test Utils，运行 lint、类型检查、测试和构建。
2. 再把构建镜像迁移到 Node 24 LTS，并同步 `@types/node@24`；随后升级 jsdom 30。
3. ESLint 10 与 `@eslint/js` 10 单独一个批次，检查新增诊断，不直接执行不受审查的批量自动修复。

## 后端

| 组件 | 基线 | 2026-09-02 最新稳定版 | 兼容性与项目判断 | 分类 |
|---|---:|---:|---|---|
| [Python](https://www.python.org/doc/versions/) | `>=3.11,<3.14`；镜像 `python:3.11-slim` | **3.14.7**；可用目标 **3.13.15** | Python 3.14 是全局最新稳定版，但 PaddlePaddle 3.3.1 只发布 CPython 3.9–3.13 wheel。Python 3.13.15 是本项目实际能采用的最新运行时。[3.13.15](https://www.python.org/downloads/release/python-31315/)、[PaddlePaddle 3.3.1 files](https://pypi.org/project/paddlepaddle/3.3.1/#files) | 3.13 **需迁移验证**；3.14 **暂缓** |
| [FastAPI](https://pypi.org/pypi/fastapi/json) | 0.141.1 | **0.141.1** | 已是最新；要求 Python `>=3.10`。 | 无需升级 |
| [Uvicorn](https://pypi.org/pypi/uvicorn/json) | 0.34.3 | **0.52.4** | 要求 Python `>=3.10`。跨越多个 0.x 次版本，官方发布记录包含协议实现、启动失败和可选依赖调整，必须验证生命周期、上传、超时和容器信号。[官方发布说明](https://uvicorn.dev/release-notes/) | **需迁移验证** |
| [Pydantic](https://pypi.org/pypi/pydantic/json) | 2.11.5 | **2.13.5** | 仍在 v2，但应验证请求校验、序列化、Decimal、错误响应以及 ORM 转换。 | **需迁移验证** |
| [pydantic-settings](https://pypi.org/pypi/pydantic-settings/json) | 2.9.1 | **2.15.0** | 应与 Pydantic 同批，重点验证 `.env`、环境变量、空值、列表和布尔解析。 | **需迁移验证** |
| [SQLAlchemy](https://pypi.org/pypi/sqlalchemy/json) | 2.0.41 | **2.0.52** | 同属 2.0 稳定线，但数据库属于持久状态边界，需要验证 SQLite 事务、Session、约束和并发。2.1.0rc1 不计为稳定版。 | **需迁移验证** |
| [Alembic](https://pypi.org/pypi/alembic/json) | 1.16.1 | **1.19.1** | 要求 Python `>=3.10`、SQLAlchemy `>=1.4.23`。与 SQLAlchemy 同批，验证旧数据库升级和空库初始化。 | **需迁移验证** |
| [HTTPX](https://pypi.org/pypi/httpx/json) | 0.28.1 | **0.28.1** | 已是最新；不要采用 1.0 dev 预览版。 | 无需升级 |
| [openpyxl](https://pypi.org/pypi/openpyxl/json) | 3.1.5 | **3.1.5** | 已是最新。迁移 Python 3.13 时必须用真实报销模板验证合并单元格、样式、行列宽、打印设置和公式。 | 无需升级；随 Python 验证 |
| [PyMuPDF](https://pypi.org/pypi/pymupdf/json) | 未安装 | **1.28.2** | 当前 PDF 流程使用 pypdf + pypdfium2，现有功能没有显示出必须增加第二套 PDF 引擎的缺口；这不是“升级”。 | **暂缓引入** |
| [PaddleOCR](https://pypi.org/pypi/paddleocr/json) | 3.7.0 | **3.7.0** | 已是最新；Python `>=3.8`，PaddleOCR 3.x 要求 PaddlePaddle 3.x。[官方安装说明](https://www.paddleocr.ai/main/en/version3.x/installation.html) | 无需升级 |
| [PaddlePaddle](https://pypi.org/pypi/paddlepaddle/json) | 3.3.1 | **3.3.1** | 已是最新；官方 wheel 覆盖 CPython 3.9–3.13，当前部署的 Linux x86-64 CPU 路径受支持。 | 无需升级 |
| [Ruff](https://pypi.org/pypi/ruff/json) | 0.11.12 | **0.16.5** | Ruff 在 1.0 前允许次版本包含规则、配置或格式变化，应审阅新增诊断与格式差异。[官方版本策略](https://docs.astral.sh/ruff/versioning/) | **需迁移验证** |
| [pytest](https://pypi.org/pypi/pytest/json) | 9.1.1 | **9.1.1** | 已是最新；要求 Python `>=3.10`。 | 无需升级 |
| [uv](https://pypi.org/pypi/uv/json) | 0.11.21 | **0.12.9** | uv 0.12 是可能包含破坏性变化的次版本，锁文件格式也需要检查。[0.12.0 发布说明](https://github.com/astral-sh/uv/releases/tag/0.12.0)、[uv 版本策略](https://docs.astral.sh/uv/reference/policies/versioning/) | **需迁移验证** |

### 后端推荐批次

1. SQLAlchemy 2.0.52 + Alembic 1.19.1：在复制的旧 SQLite 数据库和空库上分别执行升级。
2. Pydantic 2.13.5 + pydantic-settings 2.15.0：验证 API schema、校验错误和所有环境配置。
3. Uvicorn 0.52.4：单独验证启动/停止、健康检查、大文件上传、客户端断开、Nginx 超时和进程信号。
4. Ruff 0.16.5 与 uv 0.12.9：作为工具链批次，审阅 lint 与 `uv.lock` diff，再执行 frozen sync 和 Docker 构建。
5. Python 3.13.15：最后迁移，重点跑真实 OCR 和 Excel 回归及内存/冷启动测试；保留 `<3.14` 上限。

## Docker、Node、Python 与 Nginx 基础版本

| 组件 | 基线/当前 | 最新稳定状态 | 建议 | 分类 |
|---|---|---|---|---|
| [Node.js](https://nodejs.org/en/about/previous-releases) | 构建镜像 `node:22-alpine` | **26.8.1 Current**；**24.20.0 LTS** | 生产构建使用 LTS，而不是 Current。升级到 `node:24-alpine`（可复现构建可进一步固定为 `24.20.0-alpine`/digest）。Node 官方明确建议生产只使用 Active/Maintenance LTS。 | Node 24 **需迁移验证**；Node 26 **暂缓** |
| [Python Docker Official Image](https://hub.docker.com/_/python) | `python:3.11-slim` | 3.14.7；兼容目标 `3.13.15-slim` | OCR 使用 manylinux/glibc wheel，继续选 Debian slim，不换 Alpine。完整固定可用 `python:3.13.15-slim-trixie`。 | **需迁移验证** |
| [uv Docker image](https://docs.astral.sh/uv/guides/integration/docker/) | `ghcr.io/astral-sh/uv:0.11.21` | **0.12.9** | 升级前后比较 `uv.lock`，要求 `uv lock --check`、`uv sync --frozen` 和镜像构建通过；之后可固定 digest。 | **需迁移验证** |
| [Nginx](https://nginx.org/en/download.html) | `nginx:1.27-alpine` | **1.30.4 stable**；1.31.4 mainline | 内部工具优先 stable `nginx:1.30.4-alpine`，不要仅为追新上 mainline。验证 `nginx -t`、非 root UID 101、只读根文件系统、tmpfs、上传、SPA fallback、CSP 和代理超时。 | stable **需迁移验证**；mainline **暂缓** |
| [Docker Engine](https://docs.docker.com/engine/release-notes/29/) | 本机 29.7.2 | **29.7.2** | 本机已是最新，无须修改仓库；其他部署主机升级到 29 前注意最低 Engine API v1.44。 | 无需升级 |
| [Docker Compose](https://github.com/docker/compose/releases/tag/v5.5.0) | 本机 v5.5.0 | **v5.5.0** | 本机已是最新。Compose 文件已经使用现代 Compose Specification，没有需要删除的顶层 `version`。 | 无需升级 |

浮动大版本镜像会在不同日期解析到不同系统包。若追求强可复现性，可在验证后固定“完整版本 + 发行版 + digest”；但必须同时建立定期刷新机制，否则 digest 固定会错过安全修复。对这个小型项目，固定完整版本并在发版前重建验证，已经是较简单的折中。

## 实施优先级

### 可直接升级

优先做 Vue Router 5、eslint-plugin-vue、typescript-eslint、globals、Vue Test Utils。这些变化小，而且现有前端测试、类型检查和构建能给出明确反馈。

### 需迁移验证

建议按下面顺序拆分，避免一个提交同时改变全部变量：

1. Node 24 + `@types/node@24` + jsdom 30；
2. ESLint 10 + `@eslint/js` 10；
3. SQLAlchemy/Alembic；
4. Pydantic/pydantic-settings；
5. Uvicorn；
6. Ruff/uv；
7. Python 3.13；
8. Nginx 1.30 stable。

每批至少运行：

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build

cd ../backend
uv sync --frozen --extra dev --extra ocr
uv run ruff check .
uv run pytest

cd ..
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

Python、Paddle、openpyxl 或容器基础镜像变化时，还要运行真实票据 OCR 和真实模板 Excel 的回归测试；仅靠单元测试不够。

### 暂缓

- **Node 26**：仍为 Current，等进入 LTS 后再评估。
- **Python 3.14**：PaddlePaddle 3.3.1 没有 cp314 wheel。
- **Nginx 1.31 mainline**：当前没有必须使用的 mainline 功能。
- **PyMuPDF**：当前实现没有需要第二套 PDF 引擎解决的问题。
- **SQLAlchemy 2.1 RC、HTTPX 1.0 dev**：预发布版本不进入生产依赖。

## 最终建议

可以升级，但不需要换技术栈。最合适的新基线是：

- 前端：Vue 3.5 + Vue Router 5 + Vite 8 + TypeScript 7 原生检查/TypeScript 6 工具桥接 + Node 24 LTS；
- 后端：Python 3.13 + FastAPI 0.141 + Pydantic 2.13 + SQLAlchemy 2.0 + PaddleOCR 3.7；
- 部署：Docker Engine 29 / Compose 5 + Nginx 1.30 stable。

这个组合比追 Node 26、Python 3.14 或 Nginx mainline 更适合当前项目：核心依赖有正式支持，变化可以由现有测试、真实 OCR 样本和 Excel 模板回归覆盖。

## 升级后快速复核

复核日期：2026-09-03。再次以 `frontend/package.json`、`backend/pyproject.toml`、两个 Dockerfile 和锁文件为准，对 npm/PyPI 官方索引及各项目官方发布页复核。结论是：**运行依赖没有新的常规升级项；遗漏的构建依赖 Hatchling 已从 1.27.0 升级到 1.32.0。**

| 分类 | 组件 | 当前 → 最新稳定版 | 复核结论 |
|---|---|---|---|
| **已完成** | [Hatchling](https://pypi.org/project/hatchling/) | **1.32.0** | 仅用于构建 wheel；升级后已完成 `uv lock --check`、`uv build`、frozen sync 与整仓测试。 |
| **因上游兼容应锁定** | [TypeScript 7 原生编译器 / TS6 桥接](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/) | `typescript` 7.0.2 / `@typescript/typescript6` 6.0.2，均为最新 | 当前别名布局正是 TypeScript 官方推荐的并行方案：`@typescript/native: npm:typescript@7.0.2` 提供原生 `tsc`，`typescript: npm:@typescript/typescript6@6.0.2` 为依赖编译器 API 的工具保留 TS6。实测 `tsc` 为 7.0.2，`tsc6` 为 6.0.3（桥接包内部允许的 TS6 补丁版）。[typescript-eslint 官方支持范围](https://typescript-eslint.io/users/dependency-versions/)仍是 TypeScript `>=4.8.4 <6.1.0`，所以桥接暂不能删除。 |
| **因上游兼容应锁定** | [OpenCV](https://pypi.org/project/opencv-contrib-python/) | 4.10.0.84 → 全局最新 **5.0.0.93** | 不升级。PaddleOCR 3.7.0 带入的 PaddleX 3.7.2 精确要求 `opencv-contrib-python==4.10.0.84`，当前锁文件也解析到该版本；绕过它升级 4.14/5.0 只能作为隔离 A/B 实验，不能进入主环境。上游精确约束见 [PaddleX 官方源码](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/setup.py)。 |
| **因上游兼容应锁定** | [Python](https://www.python.org/downloads/release/python-3147/) | 镜像 3.13.15；全局最新 **3.14.7** | 继续保留 `>=3.11,<3.14`。PaddlePaddle 3.3.1 的 [PyPI 官方文件](https://pypi.org/project/paddlepaddle/3.3.1/#files)没有 CPython 3.14 wheel；3.13.15 已是当前 OCR 组合能采用的最新 Python。 |
| **因运行时匹配应锁定** | [Node.js](https://nodejs.org/en/about/previous-releases)、[@types/node](https://registry.npmjs.org/%40types%2Fnode/latest) | 镜像/LTS 24.20.0，类型 24.13.3；全局最新 Node **26.8.1 Current**、类型 **26.4.1** | 生产构建继续使用 Node 24 LTS，`@types/node` 继续留在 24.x；不应为了 npm 的全局 `latest` 引入 Node 26 API。Node 官方仍建议生产只用 Active/Maintenance LTS。 |
| **可升级但收益低** | [Nginx](https://nginx.org/en/download.html) | stable **1.30.4**；mainline **1.31.4** | 当前已是 stable 最新版，并包含官方列出的相关安全修复；项目没有依赖 mainline 新功能，暂不上 1.31.4。 |
| **可升级但收益低** | Docker 镜像固定方式 | 完整 tag，未固定 digest | Node/Python/Nginx/uv 均已固定完整 tag。再固定 digest 可增强可复现性，但必须配套定期安全刷新；当前小项目收益有限。Docker Engine [29.7.2 官方发布说明](https://docs.docker.com/engine/release-notes/29/)与 Compose [v5.5.0 官方发布](https://github.com/docker/compose/releases/tag/v5.5.0)显示本机工具也已是当前稳定版。 |
| **已最新** | Vue / Vite / Router / UI 与前端工具 | Vue 3.5.42、Vite 8.2.2、Router 5.3.0、Element Plus 2.14.5、ESLint 10.9.1、`@eslint/js` 10.0.1、eslint-plugin-vue 10.10.0、typescript-eslint 8.69.0、globals 17.12.0 | `npm outdated --json` 只报告跨运行时大版本的 `@types/node@26`；其余直接依赖均未报告更新。还包括 Pinia 4.0.3、Axios 1.20.0、plugin-vue 6.0.8、Vitest 4.1.11、vue-tsc 3.3.11、Vue Test Utils 2.5.0、jsdom 30.0.1 及两个 unplugin。版本来源为各包 [npm 官方注册表](https://registry.npmjs.org/)。 |
| **已最新** | FastAPI / Starlette / HTTP 客户端 | FastAPI 0.141.1、Starlette 1.6.0、HTTPX 0.28.1、HTTPX2 2.12.0 | 均为 PyPI 最新稳定版；保留 HTTPX2 专供 Starlette `TestClient` 的现有兼容路径。来源：[FastAPI](https://pypi.org/project/fastapi/)、[Starlette](https://pypi.org/project/starlette/)、[HTTPX](https://pypi.org/project/httpx/)、[HTTPX2](https://pypi.org/project/httpx2/)。 |
| **已最新** | 数据模型、数据库与服务端 | Pydantic 2.13.5、pydantic-settings 2.15.0、SQLAlchemy 2.0.52、Alembic 1.19.1、Uvicorn 0.52.4 | 均为 PyPI 最新稳定版，不采用 SQLAlchemy 2.1 预发布版。来源：[Pydantic](https://pypi.org/project/pydantic/)、[SQLAlchemy](https://pypi.org/project/SQLAlchemy/)、[Alembic](https://pypi.org/project/alembic/)、[Uvicorn](https://pypi.org/project/uvicorn/)。 |
| **已最新** | OCR、文件与测试工具 | PaddleOCR 3.7.0、PaddlePaddle 3.3.1、pypdf 6.16.2、openpyxl 3.1.5、Pillow 12.3.0、pytest 9.1.1、Ruff 0.16.5 | 除上表有意锁定的 OpenCV 外，直接依赖均为 PyPI 最新稳定版。来源：[PaddleOCR](https://pypi.org/project/paddleocr/)、[PaddlePaddle](https://pypi.org/project/paddlepaddle/)、[pypdf](https://pypi.org/project/pypdf/)、[openpyxl](https://pypi.org/project/openpyxl/)、[Pillow](https://pypi.org/project/pillow/)、[pytest](https://pypi.org/project/pytest/)、[Ruff](https://pypi.org/project/ruff/)。 |
| **已最新** | [uv 容器工具](https://docs.astral.sh/uv/guides/integration/docker/) | 镜像 **0.12.9** | Docker 构建使用的 uv 已是最新稳定版。 |

本机开发环境与容器只剩 Node 版本有意不同：mise Node 是 26.5.0，前端镜像固定为 Node 24.20.0 LTS。本机与容器的 uv 均为 0.12.9，`backend/.python-version` 和后端镜像均固定 Python 3.13.15；后端 `.venv` 已由 uv 重新创建并使用该解释器。Node 26 当前仅用于额外兼容验证，生产构建仍以容器内 Node 24 的结果为准。

本次已同步依赖声明、Python 版本文件和开发文档；`uv.lock` 的解析结果无需变化。
