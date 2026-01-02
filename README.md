# Lion Management Service (AIOE后台管理服务)

## 开发环境

- Python 3.11+
- uv 包管理
- Aerich 数据库版本管理 (Tortoise ORM)
- Sanic Web Framework

## 快速开始

1.  **安装依赖**

    ```bash
    uv sync
    ```

2.  **配置环境变量**

    在 `settings` 目录下创建 `.env` 文件，参照下文的[环境变量配置](#环境变量配置)进行配置。

3.  **运行服务**

    ```bash
    python server.py
    ```

## 环境变量配置

请在 `settings/.env` 中创建以下配置（值仅为示例，请根据实际环境修改）。

### 基础配置
```env
# 运行环境: TEST, PROD, DEV
RY_ENV=DEV
# 时区
TZ=Asia/Shanghai
# 子网前缀 (可选，用于特定网络逻辑)
SUBNET_PREFIX=172.18.0
# 引擎名称/ID
RY_ENGINE_NAME=lion_engine_01
```

### 数据库 (PostgreSQL)
```env
RY_DB_HOST=127.0.0.1
RY_DB_PORT=5432
RY_DB_USER=postgres
RY_DB_NAME=lion_mgmt
RY_DB_PASS=your_password
```

### Redis (缓存与消息)
```env
RY_REDIS_HOST=127.0.0.1
RY_REDIS_PORT=6379
RY_REDIS_PASS=your_redis_password
RY_REDIS_USE_SSL=0

# Redis 数据库索引定义
RY_REDIS_DB=5       # 默认通用缓存
RY_CHAT_DB=6        # 聊天记录存储
RY_ACTION_CACHE=7   # Action 执行缓存
RY_RPC_CACHE=8      # RPC 调用缓存
```

### Kafka (消息队列)
```env
RY_KAFKA_SERVERS=127.0.0.1:9092
RY_KAFKA_USER=lion
RY_KAFKA_PASS=your_kafka_password
# 认证机制: PLAIN, SCRAM-SHA-256, SCRAM-SHA-512
RY_KAFKA_MECHANISM=SCRAM-SHA-512
# 协议: PLAINTEXT, SASL_PLAINTEXT, SASL_SSL
RY_KAFKA_PROTOCOL=SASL_PLAINTEXT

# Kafka Topics
RY_TASK_TOPIC=lion_task
RY_AUDIT_TOPIC=lion_audit
RY_RUNLOG_TOPIC=lion_runlog
RY_WR_TOPIC=lion_wr_chat
```

### 文件存储 (S3/MinIO)
```env
RY_FS_HOST=http://127.0.0.1:9000
RUSTFS_ACCESS_KEY=minioadmin
RUSTFS_SECRET_KEY=minioadmin
```

### 其他服务集成
```env
# 资源加密密钥 (32位字符)
RY_RES_KEY=your_resource_key_32_chars_here
# Job Server 地址
RY_JOB_SERVER=http://lion-job:9001
# 日志查看服务地址
VLOGS_HOST=http://lion-vl:9428
```

## 文件组织结构

```
.
├── core                    # 核心框架逻辑
│   ├── exception_handlers  # 全局异常处理
│   ├── listeners           # Sanic声明周期监听器
│   ├── middlewares         # 中间件 (认证、日志、CORS等)
│   └── response.py         # 统一响应格式
├── libs                    # 公共工具库
│   ├── security            # 安全认证 (Authentication, Authorization)
│   ├── chat.py             # 聊天功能库
│   ├── kafka.py            # Kafka 客户端封装
│   ├── supervisor.py       # 进程管理相关
│   └── utils.py            # 通用工具函数
├── models                  # Tortoise ORM 数据模型
│   ├── base.py             # 模型基类
│   └── ...                 # 各业务实体模型
├── services                # 业务逻辑模块 (Views & Services)
│   ├── ai                  # AI 相关服务
│   ├── approvement         # 审批服务
│   ├── dashboard           # 仪表盘数据
│   ├── event               # 事件处理
│   ├── mcp                 # MCP (Model Context Protocol) 服务
│   ├── user                # 用户与权限管理
│   ├── workflow            # 工作流引擎
│   └── ...
├── settings                # 配置文件
│   ├── __init__.py         # 配置加载逻辑
│   └── .env                # 环境变量 (需自行创建)
├── migrations              # 数据库迁移脚本 (Aerich)
├── server.py               # 服务启动入口
└── pyproject.toml          # 项目配置与依赖 (uv)
```

## 开发规范

### 命名规范

- 文件和文件夹使用小写字母和下划线（`snake_case`）。
- 路由名称使用小写字母和连字符，例如 `/user-profile`。
- 变量名和函数名使用小写字母和下划线（`snake_case`），类名使用大写字母开头的驼峰式（`CamelCase`）。
- 常量使用大写字母和下划线（`UPPER_SNAKE_CASE`）。

### 路由设计

- **请求方法**: 推荐所有业务接口统一使用 `POST` 请求方法（除特定资源获取可能使用 GET）。
- **模块划分**: `services` 目录下按功能划分模块，避免单个文件过于庞大。
- **响应格式**: 所有接口按照统一格式返回，使用 `core.response` 中的 `response_ok`, `response_fail` 方法。
- **参数传递**: 避免在 URL 路径中包含参数，GET 请求通过 query param 传参，POST 请求通过 JSON body 传参。

### 错误处理

- 所有 API 应返回标准化的错误响应。
- 自定义错误码统一定义在 `core/error_code.py` 或 `libs/error_code` 中。
- 异常处理逻辑位于 `core/exception_handlers`。

### 数据库交互

- 使用 `tortoise-orm` 操作数据库，尽量避免直接 SQL 操作。
- 事务操作要确保处理失败时回滚。
- 使用异步数据库操作来提高性能。
- 数据库迁移使用 `aerich`：
    - 生成迁移: `aerich migrate`
    - 执行迁移: `aerich upgrade`

### 日志记录

- 使用 Sanic 或项目封装的 logger 记录日志。
- 重要操作和错误必须记录日志。

### 代码风格

- 按照 PEP8 标准进行编码。
- 使用类型注解 (Type Hints) 提高代码可读性。
