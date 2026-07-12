# Lion Management Service

Lion Management Service 是 AIOE 后台管理服务，负责用户与租户管理、工具应用管理、剧本编排、事件接入、作战室协同、巡检报告、AI 智能体调度与 MCP 接入。代码主体是 Python 3.11 + Sanic 的异步 Web 服务，运行时通过 PostgreSQL、Redis、Kafka、对象存储、Vector/VictoriaLogs 和 lion_agent 共同完成自动化运维编排。

## 产品功能架构

### 1. 管理与安全底座

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 用户与 Token | `services/user`, `models/user.py` | 用户、API Token、登录、刷新、登出、用户信息。 |
| 租户 | `services/tenant`, `models/user.py` | 多租户创建、切换、成员管理。 |
| 角色与权限 | `services/role`, `libs/security/authorization.py` | RBAC 权限菜单、角色权限、通配符权限匹配。 |
| 安全策略 | `services/secure`, `settings.SECURE_CONFIG_DEFAULT` | 密码策略、账号锁定、MFA 配置。 |
| 系统配置 | `services/system_config`, `models/config.py` | 平台信息、图片上传、OAuth/JWT、审计与状态查询。 |
| License | `services/license`, `libs/security/authorization.py` | 机器码、授权校验、授权模块展示；部分业务蓝图按 license 动态加载。 |

认证由 `core.middlewares.security.SecurityMiddleware` 统一处理。默认路由需要登录，登录、健康检查、OAuth 回调和少量公开资源在白名单内。接口权限通过 `@requires_permission("module:action")` 装饰器声明。

### 2. 工具应用与资源管理

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 工具包上传 | `services/tools/views.py` | 接收 ZIP 工具包，解析 `config.json`，导入工具、动作、资源模板和权限。 |
| 动作管理 | `models/tool_management.py` | `Tool`、`ToolAction`、`ActionPermissions` 描述工具、版本、动作与动作权限。 |
| 资源管理 | `ToolResourcesModel`, `ToolResources` | 资源模板、资源实例、健康状态；敏感资源通过 `EncryptedCharField` 加密。 |
| 应用开发辅助 | `services/tools/views.py` | 支持文档上传、工具调试和 SSE 流式输出。 |

工具动作是剧本、定时任务、巡检和 AI 推荐的基础执行单元。涉及外部执行时通常生成 `run_id` 并投递到 Kafka 任务主题。

### 3. 剧本编排与自动化执行

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 场景与标签 | `services/scene`, `models/scene.py`, `models/tag.py` | 剧本和工具的业务分类。 |
| 剧本管理 | `services/workflow/views.py`, `models/playbook.py` | 剧本创建、编辑、版本、发布、导入导出、权限和输入参数。 |
| 定时任务 | `services/timed/views.py`, `models/timed.py` | 单次或 Cron 周期执行剧本/动作，并查询成功率和运行日志。 |
| 运行日志 | `models/runlog.py`, `libs/utils.runlog_list` | 记录节点执行结果，供作战室、巡检、定时任务和仪表盘展示。 |
| 审批 | `services/approvement`, `models/approvement.py` | 人工审核列表、待办与回调更新。 |

典型执行链路：

```text
API 创建剧本/动作执行请求
  -> 生成 run_id
  -> Kafka RY_TASK_TOPIC
  -> 外部执行引擎处理
  -> Kafka RY_RUNLOG_TOPIC / RY_WR_TOPIC
  -> ChatManager 后台任务消费
  -> WebSocket 推送到作战室或接口查询运行日志
```

### 4. 事件接入与告警处置

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 事件列表 | `services/event/views.py`, `models/event.py` | 人工事件、接入事件、事件详情、处置状态、报告。 |
| 事件接入 | `services/event_ingestion/views.py` | Kafka/Syslog 接入配置、Vector 配置生成和校验、样例解析、导入导出。 |
| 联动执行 | `EventRelatedScript` | 事件条件、字段映射、去重、并发和剧本联动配置。 |
| 日志查询 | `services/log_view/views.py` | 查询 VictoriaLogs 索引、字段和日志数据。 |

事件接入通过 Vector 将原始事件转换后写入 VictoriaLogs，并可按规则触发剧本联动。

### 5. 作战室与 AI 协同

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 作战室 | `services/warroom/views.py`, `models/warroom.py` | 公共/私聊房间、邀请、加入、收藏、消息列表。 |
| 实时消息 | `warroom_bp.websocket("/ws")`, `libs/chat.py` | WebSocket 连接管理，Redis/Kafka 消息消费与广播。 |
| AI 问答 | `services/ai/views.py`, `libs/chat.py` | 对话补全、AI 消息创建/发布、工具和剧本推荐词。 |
| Agent 流式输出 | `services/digital_employee/lion_agent_client.py`, `libs/chat.py` | 经 lion_agent 执行并将流式文本、工具调用状态桥接到 Redis Pub/Sub。 |

作战室是运行日志、AI 回复、协同讨论和事件处置的实时界面后端。

### 6. 智能体、指挥中心与 MCP

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 智能体画像 | `services/agent/views.py`, `models/command_center.py` | 智能体创建、列表、详情、更新、删除、执行与报告。 |
| 指挥中心调度 | `services/command_center` | 智能体登记、状态心跳、任务创建、进度更新、重新调度。 |
| Agent 调度决策 | `services/command_center/dispatcher.py` | 通过 lion_agent 的协调提示词和候选能力标签选择可用智能体。 |
| MCP 接入 | `services/mcp/views.py`, `services/mcp/server.py` | SSE 与 message 端点，向模型工具生态暴露服务能力。 |
| Workspace 同步 | `services/digital_employee/prompt_builder.py`, `services/digital_employee/lion_agent_client.py` | 渲染员工工作区文件并同步到 lion_agent 运行时。 |

lion_mgmt 不再内置本地 Agent SDK；旧智能体执行、作战室问答、巡检判断、调度决策和报告润色统一经 lion_agent 内部接口完成。

### 7. 巡检、报告、看板

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 自动巡检 | `services/inspection`, `models/inspection.py` | 巡检计划、类别、巡检项、通知、自定义报告和巡检结果。 |
| 报告文档 | `models/report.py`, `services/agent/views.py` | 报告模板、内容保存、对象存储上传、docx/pdf 相关处理。 |
| 仪表盘 | `services/dashboard`, `services/large_screen` | 运行概览、MTTR、剧本/应用统计、成功率和大屏配置。 |
| 全局变量 | `services/variables`, `models/variables.py` | 剧本、动作和 AI 使用的租户级变量。 |

## 技术架构

### 运行时分层

```text
客户端 / 前端
  -> Sanic HTTP / WebSocket / SSE
  -> core: 中间件、异常处理、统一响应、生命周期监听器
  -> services: 业务蓝图与流程编排
  -> libs: 安全、Kafka、对象存储、工具函数
  -> models: Tortoise ORM 数据模型
  -> 外部基础设施: PostgreSQL / Redis / Kafka / S3-MinIO / Vector / VictoriaLogs / LLM Provider / Job Engine
```

### 应用启动

`server.py` 创建 `services.create_app()` 并监听 `0.0.0.0:8000`。`create_app()` 的初始化顺序是：

1. 创建 Sanic 应用，使用 `orjson` 作为 JSON 编解码。
2. 加载 `settings/__init__.py` 配置。
3. 注册生命周期监听器，目前 `CacheListener` 负责 Redis、OAuth 配置和聊天后台任务。
4. 注册 Tortoise ORM，模型入口为 `models`，迁移由 Aerich 管理。
5. 注册 `SecurityMiddleware`，统一认证和审计日志写入。
6. 注册异常处理器，统一处理业务错误和未知错误。
7. 注册基础蓝图，再根据 License 的 `moduleList` 动态注册授权模块。

### 数据与集成

| 组件 | 用途 | 主要配置 |
| --- | --- | --- |
| PostgreSQL | 业务数据、租户、权限、剧本、事件、作战室、巡检等 | `RY_DB_HOST`, `RY_DB_PORT`, `RY_DB_USER`, `RY_DB_NAME`, `RY_DB_PASS` |
| Redis | 通用缓存、聊天消息、动作/RPC 缓存、会话对象 | `RY_REDIS_*`, `RY_REDIS_DB`, `RY_CHAT_DB`, `RY_ACTION_CACHE`, `RY_RPC_CACHE` |
| Kafka | 任务投递、审计、运行日志、作战室消息 | `RY_TASK_TOPIC`, `RY_AUDIT_TOPIC`, `RY_RUNLOG_TOPIC`, `RY_WR_TOPIC` |
| S3/MinIO | 工具包、图片、报告、系统文件 | `RY_FS_HOST`, `RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY` |
| Vector | Syslog/Kafka 接入、日志转换和转发 | `VECTOR_CFG`, `SUPERVISOR_PATH` |
| VictoriaLogs | 日志索引和查询 | `VLOGS_HOST` |
| LLM Provider | AI 问答、指挥中心调度 | `RY_AI_MODEL`, `RY_AI_LITE_MODEL`, `LLM_KEY`, `RY_AI_API_BASE` |

### 部署构建

`Dockerfile` 使用多阶段构建：

1. Builder 阶段安装 `uv`、`nuitka`、Chrome，执行 `uv sync --frozen --no-install-project --no-dev`。
2. 使用 Nuitka 将 `core/`、`libs/`、`models/`、`services/` 打包到 `dist/`。
3. Runtime 阶段复制 Vector、虚拟环境、打包产物、迁移、配置和报告资源。
4. 安装浏览器依赖、字体、bubblewrap 和 LibreOffice Writer。
5. `docker-entrypoint.sh` 等待数据库、执行 `aerich upgrade`，再启动传入命令或回退到 `python server.py`。

GitHub Actions 的 `.github/workflows/docker-image.yml` 在 `main` 分支和 `v*` tag 构建并推送 `linux/amd64,linux/arm64` 镜像到 `reg.hongcloud.net/lion_mgmt`。

## 快速开始

### 环境要求

- Python `>=3.11,<4`
- `uv`
- PostgreSQL
- Redis
- Kafka
- S3/MinIO 兼容对象存储
- 可选：Vector、VictoriaLogs、外部 Job Engine、LLM API

### 安装依赖

```bash
uv sync
```

### 配置环境变量

开发环境下设置 `RY_ENV=DEV` 后，服务会尝试读取 `settings/.env`。

```env
RY_ENV=DEV
TZ=Asia/Shanghai
RY_REAL_IP_HEADER=x-real-ip
RY_SECRET_KEY=change_me
RY_RES_KEY=32_chars_resource_key_here

RY_DB_HOST=127.0.0.1
RY_DB_PORT=5432
RY_DB_USER=postgres
RY_DB_NAME=lion_mgmt
RY_DB_PASS=postgres

RY_REDIS_HOST=127.0.0.1
RY_REDIS_PORT=6379
RY_REDIS_PASS=
RY_REDIS_DB=5
RY_CHAT_DB=6
RY_ACTION_CACHE=7
RY_RPC_CACHE=8

RY_KAFKA_SERVERS=127.0.0.1:9092
RY_KAFKA_PROTOCOL=PLAINTEXT
RY_KAFKA_USER=
RY_KAFKA_PASS=
RY_KAFKA_MECHANISM=
RY_TASK_TOPIC=lion_task
RY_AUDIT_TOPIC=lion_audit
RY_RUNLOG_TOPIC=lion_runlog
RY_WR_TOPIC=lion_wr_chat

RY_FS_HOST=http://127.0.0.1:9000
RUSTFS_ACCESS_KEY=minioadmin
RUSTFS_SECRET_KEY=minioadmin
RY_FS_SYS=system
RY_FS_USER=user

RY_JOB_SERVER=http://lion-job:9001
VLOGS_HOST=http://lion-vl:9428

RY_SERVICE_URL=http://localhost:8000
RY_AI_MODEL=aioe
RY_AI_LITE_MODEL=aioe-lite
LLM_KEY=your_llm_key
RY_AI_API_BASE=https://oapi.hongcloud.net/v1
RY_AGENT_MODEL=openai-proxy/gpt-4o
RY_AGENT_CONTEXT_WINDOW=8000
```

`RY_AI_LITE_MODEL` 仅用于指挥中心自动选择员工，未配置时默认使用 `aioe-lite`；数字员工正式执行仍使用 `RY_AI_MODEL`。

### 数据库迁移

```bash
aerich upgrade
```

模型变更后生成迁移：

```bash
aerich migrate
```

### 启动服务

```bash
python server.py
```

默认监听 `http://0.0.0.0:8000`。

## 目录结构

```text
.
├── core/                 # Sanic 框架层：中间件、监听器、异常处理、响应封装
├── libs/                 # 公共库：安全、Kafka、对象存储、Supervisor、工具函数
├── models/               # Tortoise ORM 模型和多租户基础模型
├── services/             # 业务模块，每个模块通常以 views.py 暴露 Sanic Blueprint
├── settings/             # 运行配置、环境变量读取、Tortoise/Redis/Kafka/日志配置
├── migrations/           # Aerich 数据库迁移
├── Dockerfile            # Nuitka + Vector 镜像构建
├── docker-entrypoint.sh  # 容器启动前数据库等待与迁移
├── server.py             # 服务入口
└── pyproject.toml        # uv 项目依赖和 Aerich 配置
```

## 开发约定

- 新业务接口放在 `services/<module>/views.py`，复杂逻辑可拆到同模块 `logic.py`。
- 新数据模型放在 `models/` 并继承 `BaseModel`，常规查询需要同时过滤 `deleted=False` 和 `tenant_id`。
- API 成功响应使用 `core.response.response_ok()`，业务错误抛 `CustomError(ErrorCode.X)`。
- 需要鉴权的接口使用 `@requires_permission("module:c|r|u|d")`；不要在接口内绕过统一中间件。
- 新增可授权模块时，同步检查 `services/__init__.py` 的蓝图注册和 License 模块名。
- 涉及外部执行的功能优先走 Kafka 任务主题和运行日志链路，保持 `run_id` 可追踪。
- 敏感资源不要明文落库；资源凭证优先使用 `EncryptedCharField` 或环境变量。
- 项目当前未提供正式测试套件；代码改动至少运行针对性导入/编译检查，文档改动至少运行 `git diff --check`。

## 维护提示

- `README.md` 面向使用者和维护者，描述产品功能、架构和运行方式。
- `AGENTS.md` 面向后续编码代理，描述真实技术栈、模块边界、开发规则和验证要求。
- 不要把示例环境变量改成真实密钥；真实凭证应来自环境变量或部署平台 Secret。
