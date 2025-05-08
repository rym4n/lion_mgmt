# AIOE后台管理服务

# 开发环境

- Python 3.11
- uv 包管理
- Aerich 数据库版本管理

# 文件组织结构

```
.
├── services                  # 接口业务逻辑
│   ├── __init__.py
│   ├── demo                  # demo模块
│   │   ├── __init__.py
│   │   └── views.py          # demo蓝图
│   └── user                  # 用户模块
│       ├── __init__.py
│       └── views.py          # 用户蓝图
├── models                    # 统一存放tortiose-orm模型
│   ├── __init__.py           # 配置统一导入，便于aerich引用
│   ├── user.py               # 定义具体模型
├── settings
│   ├── __init__.py           # 配置
│   └── .env                  # 本地开发环境的环境变量配置
├── core
│   ├── error_code.py         # 错误码
│   ├── exception_handlers    # 异常处理器
│   │   ├── base.py           # 异常处理基类
│   │   ├── error_code.py     # 错误码异常处理
│   │   ├── __init__.py
│   │   └── unknown.py        # 未知异常处理
│   ├── __init__.py
│   ├── listeners             # 监听
│   │   ├── __init__.py
│   │   ├── base.py           # 监听基类
│   │   └── cache.py          # 缓存监听
│   ├── middlewares           # 中间件
│   │   ├── __init__.py
│   │   ├── base.py           # 中间件基类
│   │   ├── cache.py          # 内存或redis中间件
│   │   └── timer.py          # 计时中间件
│   └── response.py           # 响应函数
├── libs                      # 公共库
│   ├── config.py             # 配置文件处理方法
│   ├── error_code            # 错误码处理
│   │   ├── __init__.py
│   │   ├── enum.py           # 错误码枚举
│   │   └── exception.py      # 错误码异常
│   └── __init__.py
├── logs                      # 日志文件存储路径
└── server.py                 # 启动文件
```

本地配置开发环境的时候，自行在 `src/settings` 目录下创建 `.env` 文件，配置内容如下：
```.env
RY_DB_HOST=xxxxx
RY_DB_PORT=5432
RY_DB_USER=postgre
RY_DB_PASS=xxxxx
RY_DB_NAME=lion_mgmt
```

> 环境变量没有配置RY_REDIS_HOST的时候，默认会使用内存作为缓存空间，既方便在本地调试，也适用于一些不方便部署redis等缓存中间件的情况

# 开发规范

## 命名规范

- 文件和文件夹使用小写字母和下划线（`snake_case`）。
- 路由名称使用小写字母和连字符，例如 `/user-profile`。
- 变量名和函数名使用小写字母和下划线（`snake_case`），类名使用大写字母开头的驼峰式（`CamelCase`）。
- 常量使用大写字母和下划线（`UPPER_SNAKE_CASE`）。

## 路由设计

- 所有接口都统一用POST请求方法。
- app下目录按按功能划分，避免单个文件过于庞大。
- 所有接口按照统一格式返回，使用 response_ok、response_fail 方法，错误码要明确定义。
- 避免在URL路径中包含参数，只有GET请求可以通过query param传参，其他请求只能通过body传参，且一般为json格式。

## 错误处理

- 所有API应返回标准化的错误响应。
- 自定义错误码统一定义在 `core/error_code.py`

示例：

```python
@demo.route("/error", methods=["GET"])
async def error(request: Request) -> HTTPResponse:
    """演示错误码异常"""
    from core.error_code import ErrorCode
    from libs.error_code import CustomError
    raise CustomError(ErrorCode.Forbidden)
```

## 日志记录

- 使用 sanic.log 模块的 logger 记录日志。
- 配置日志记录等级为 `INFO`，对于重要错误或警告记录为 `ERROR`。

示例：
```python
from sanic.log import logger

@app.route('/log')
async def log_example(request):
    logger.info('This is an info log')
    return json({"message": "Logged info"})
```

## 数据库交互

- 使用tortoise-orm操作数据库，尽量避免sql直接操作。
- 事务操作要确保处理失败时回滚。
- 使用异步数据库操作来提高性能。

## 代码风格

- 按照 PEP8 标准进行编码，使用 4 个空格进行缩进。
- 每个文件结尾不要有多余空行。
- 使用类型注解提高代码可读性。
