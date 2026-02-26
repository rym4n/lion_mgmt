# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Lion Management Service (AIOE后台管理服务) - 基于 Sanic 的异步 Web 服务，提供 AI 运维编排引擎的后台管理功能。

## 常用命令

### 依赖管理
- **安装依赖**: `uv sync`
- **添加依赖**: `uv add <package>`
- **更新依赖**: `uv sync --upgrade`

### 运行服务
- **启动服务**: `python server.py` (默认运行在 http://0.0.0.0:8000)
- **开发模式**: `python server.py` (Sanic 自带热重载)

### 数据库迁移
- **生成迁移**: `aerich migrate` (检测模型变更并生成迁移文件)
- **应用迁移**: `aerich upgrade` (执行待应用的迁移)
- **回滚迁移**: `aerich downgrade` (回滚上一次迁移)
- **查看历史**: `aerich history` (查看迁移历史)

### 测试
- 项目当前没有正式的测试套件 (tests 目录为空)

## 架构概览

### 技术栈
- **Web 框架**: Sanic 24.12.0 (异步 Python Web 框架)
- **ORM**: Tortoise ORM with asyncpg (PostgreSQL 异步驱动)
- **缓存**: Redis (通过 aiocache)
- **消息队列**: Kafka (通过 aiokafka)
- **包管理**: uv (使用阿里云镜像源)
- **序列化**: orjson (高性能 JSON 库)

### 应用初始化流程
应用通过 `services/__init__.py` 中的 `create_app()` 函数创建，按以下顺序初始化：
1. 创建 Sanic 应用实例 (使用 orjson 作为 JSON 序列化器)
2. 加载配置 (`settings/__init__.py`)
3. 注册监听器 (生命周期事件)
4. 注册 Tortoise ORM (数据库连接)
5. 注册中间件 (认证、日志、CORS 等)
6. 注册异常处理器
7. 注册蓝图 (业务模块路由)
8. 根据 License 动态加载授权模块

### 目录结构

```
├── core/                    # 框架核心层
│   ├── error_code.py        # 全局错误码定义
│   ├── exception_handlers/  # 全局异常处理器
│   ├── listeners/           # Sanic 生命周期监听器
│   ├── middlewares/         # 中间件 (认证、日志、CORS)
│   └── response.py          # 统一响应格式封装
│
├── libs/                    # 共享工具库
│   ├── security/            # 安全模块
│   │   ├── authentication.py  # JWT 认证
│   │   └── authorization.py   # 权限校验与 License 管理
│   ├── chat.py              # 聊天功能封装
│   ├── kafka.py             # Kafka 客户端封装
│   ├── supervisor.py        # 进程管理工具
│   └── utils.py             # 通用工具函数 (加密、解密等)
│
├── models/                  # Tortoise ORM 数据模型
│   ├── base.py              # BaseModel (所有模型的基类)
│   ├── user.py              # 用户与租户模型
│   ├── event.py             # 事件模型
│   ├── playbook.py          # 剧本模型
│   ├── warroom.py           # 作战室模型
│   └── ...                  # 其他业务实体模型
│
├── services/                # 业务逻辑模块 (按功能划分)
│   ├── __init__.py          # 应用工厂函数 create_app()
│   ├── user/                # 用户与权限管理
│   ├── workflow/            # 工作流引擎
│   ├── event/               # 事件处理
│   ├── warroom/             # 作战室 (聊天与协作)
│   ├── ai/                  # AI 服务集成
│   ├── mcp/                 # MCP (Model Context Protocol)
│   ├── approvement/         # 审批流程
│   └── ...                  # 其他业务模块
│
├── settings/                # 配置管理
│   ├── __init__.py          # 配置加载与环境变量
│   └── .env                 # 环境变量文件 (需自行创建)
│
├── migrations/              # Aerich 数据库迁移脚本
├── server.py                # 应用入口
└── pyproject.toml           # 项目配置与依赖定义
```

## 开发规范

### 响应格式
所有 API 必须使用 `core.response` 中的统一响应格式：

**成功响应**:
```python
from core.response import response_ok
return response_ok(data={"key": "value"})
# 返回: {"code": 0, "data": {"key": "value"}}
```

**失败响应**:
```python
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError

# 方式1: 抛出异常 (推荐)
raise CustomError(ErrorCode.PERMISSION_DENIED)

# 方式2: 直接返回
from core.response import response_fail
return response_fail(ErrorCode.PERMISSION_DENIED, status=403)
```

### 数据库操作

#### 模型基类
所有模型继承自 `models.base.BaseModel`，自动包含以下字段：
- `id`: UUID 主键 (自动生成)
- `ctime`: 创建时间 (自动设置)
- `utime`: 更新时间 (自动更新)
- `deleted`: 软删除标识 (默认 False)
- `tenant_id`: 租户 ID (多租户支持)

#### 软删除
**重要**: 所有查询必须过滤 `deleted=False`，避免查询到已删除的数据：
```python
# 正确
users = await User.filter(deleted=False, tenant_id=tenant_id).all()

# 错误 - 会包含已删除的数据
users = await User.filter(tenant_id=tenant_id).all()
```

删除操作应使用软删除：
```python
user.deleted = True
await user.save()
```

#### 多租户
查询时必须考虑 `tenant_id` 隔离：
```python
# 从 session 获取租户 ID
tenant_id = request.ctx.session.get("tenant_id")
data = await Model.filter(deleted=False, tenant_id=tenant_id).all()
```

#### 事务处理
使用 `in_transaction()` 确保原子性：
```python
from tortoise.transactions import in_transaction

async with in_transaction() as conn:
    await Model1.create(..., using_db=conn)
    await Model2.create(..., using_db=conn)
```

#### 加密字段
使用 `EncryptedCharField` 存储敏感信息 (自动加密/解密)：
```python
from models.base import EncryptedCharField

class Secret(BaseModel):
    token = EncryptedCharField(max_length=255)
```

### 认证与权限

#### 认证
- 路由默认需要认证 (通过 `SecurityMiddleware`)
- Session 数据存储在 `request.ctx.session` (由中间件注入)
- 访问用户信息: `request.ctx.session['account_info']`

#### 权限校验
使用 `@requires_permission` 装饰器进行权限控制：
```python
from libs.security.authorization import check_permissions

@app.post("/api/resource")
async def handler(request: Request):
    await check_permissions(request, "module:action")
    # 业务逻辑
```

权限格式: `module:action` (例如: `user:create`, `role:*`)
- 支持通配符: `role:*` 表示 role 模块的所有权限
- 支持多值: `role:read,write` 表示多个操作权限

### 命名规范
- **文件/变量**: `snake_case`
- **类名**: `PascalCase`
- **常量**: `UPPER_SNAKE_CASE`
- **路由**: `kebab-case` (例如: `/user-profile`)

### 导入顺序
```python
# 1. 标准库
import os
import time

# 2. 第三方库
from sanic import Request
from tortoise import fields

# 3. 本地导入
from core.response import response_ok
from libs.utils import encrypt_aes256_gcm
from models import User
from services.user.logic import create_user
```

### 蓝图与路由
- 每个业务模块在 `services/<module>/` 下组织
- `views.py`: 定义路由和请求处理
- `logic.py` (可选): 复杂业务逻辑
- 使用 Sanic Blueprint 组织路由：
```python
from sanic import Blueprint

user_bp = Blueprint("user", url_prefix="/user")

@user_bp.post("/create")
async def create_user(request: Request):
    return response_ok()
```

### Redis 数据库分配
项目使用多个 Redis 数据库索引，避免混用：
- `RY_REDIS_DB=5`: 默认通用缓存
- `RY_CHAT_DB=6`: 聊天记录存储
- `RY_ACTION_CACHE=7`: Action 执行缓存
- `RY_RPC_CACHE=8`: RPC 调用缓存

### Kafka Topics
- `RY_TASK_TOPIC`: 任务消息
- `RY_AUDIT_TOPIC`: 审计日志
- `RY_RUNLOG_TOPIC`: 运行日志
- `RY_WR_TOPIC`: 作战室聊天消息

## 环境配置

在 `settings/.env` 中配置以下环境变量 (参考 README.md 中的详细说明)：

**必需配置**:
- `RY_ENV`: 运行环境 (DEV/TEST/PROD)
- `RY_DB_*`: PostgreSQL 连接信息
- `RY_REDIS_*`: Redis 连接信息
- `RY_KAFKA_*`: Kafka 连接信息

**可选配置**:
- `RY_FS_HOST`: S3/MinIO 文件存储
- `RY_JOB_SERVER`: Job Server 地址
- `RY_RES_KEY`: 资源加密密钥 (32位)

## 特殊注意事项

### License 模块加载
部分业务模块 (event, workflow, ai, mcp 等) 需要 License 授权才能加载。License 验证逻辑在 `services/__init__.py` 的 `register_blueprints()` 中。

### 异步操作
所有数据库、Redis、Kafka 操作都是异步的，必须使用 `await`：
```python
# 正确
user = await User.get(id=user_id)

# 错误
user = User.get(id=user_id)  # 返回 coroutine 对象
```

### 类型注解
代码使用 Python 3.11+ 的类型注解，建议保持一致：
```python
from typing import Optional

async def get_user(user_id: str) -> Optional[User]:
    return await User.filter(id=user_id, deleted=False).first()
```
