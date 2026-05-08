# aioe-app-dev

AIOE 应用动作代码生成规范 - 告诉 AI 如何生成符合 aioe_app 格式的 Python 代码。

## 概述

当用户要求生成 AIOE 应用动作代码时，请按照本文档的规范生成代码。

**工作流程**：
1. 用户提出需求（例如："帮我查看1.1.1.1的服务器系统状态"）
2. nanobot 理解并执行需求（使用各种工具：agent-browser、bash 等）
3. nanobot 返回执行结果给用户确认
4. 用户确认后，要求生成动作代码
5. nanobot 根据本规范，把执行过程转换成 Python 代码

## 代码规范

### 重要约束：禁止使用 agent-browser

**所有涉及 Web 访问、HTTP 请求的场景，必须使用 `requests` 库实现，禁止使用 `agent-browser`。**

理由：
- `requests` 库更轻量、稳定，无需额外的浏览器进程
- 避免浏览器依赖和环境配置问题
- 更适合自动化和批量操作
- 性能更好，资源占用更少

如果用户的执行过程中使用了 agent-browser，在生成代码时应转换为 requests 实现：
- `agent-browser open <url>` → `requests.get(url)` 或 `requests.post(url)`
- 登录操作 → 使用 `requests.Session()` 保持会话
- 截图需求 → 改为获取 HTML 内容或 API 数据
- 页面交互 → 改为直接调用后端 API

### 文件头部

所有代码文件必须以以下内容开头：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""
```

### 函数签名

所有动作函数必须使用以下签名：

```python
def function_name(params, assets, context):
    """函数文档字符串"""
    pass
```

**参数说明**：
- `params` (dict) - 调用参数
- `assets` (dict) - 资源配置，从 config.json 的 resource 部分获取
- `context` (dict) - 上下文信息

### 返回值格式

**成功时返回**：
```python
{
    "code": 0,
    "msg": "",
    "data": result_data  # 可以是任何类型：字符串、数字、字典、列表等
}
```

**失败时返回**：
```python
{
    "code": -1,
    "msg": "错误信息描述"
}
```

### 异常处理

必须使用 try-except 处理异常：

```python
def function_name(params, assets, context):
    """函数文档字符串"""

    try:
        # 业务逻辑
        result = do_something()
        json_ret = {"code": 0, "msg": "", "data": result}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

### 资源配置获取

从 assets 获取配置参数，必须提供默认值：

```python
# 字符串类型
server_ip = assets.get('server_ip', '')

# 数字类型
port = assets.get('port', 0)
timeout = assets.get('timeout', 30)

# 布尔类型
enabled = assets.get('enabled', False)
```

## 代码模板

### 1. 基础模板

适用于简单操作或自定义逻辑：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""

import time


def health_check(params, assets, context):
    """健康检查"""
    json_ret = {"code": 0, "msg": "", "data": ""}
    return json_ret


def your_function(params, assets, context):
    """你的函数描述"""

    # 从 assets 获取配置参数
    param1 = assets.get('param1', 'default_value')
    param2 = assets.get('param2', 0)

    try:
        # 执行业务逻辑
        result = f"处理结果: {param1}"

        json_ret = {"code": 0, "msg": "", "data": result}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

### 2. HTTP 请求模板（替代 agent-browser）

适用于 Web 访问、API 调用、登录等操作（使用 requests 库）：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""

import requests


def health_check(params, assets, context):
    """健康检查"""
    json_ret = {"code": 0, "msg": "", "data": ""}
    return json_ret


def web_request(params, assets, context):
    """Web 请求操作"""

    # 从 assets 获取配置参数
    url = assets.get('url', '')
    username = assets.get('username', '')
    password = assets.get('password', '')
    timeout = assets.get('timeout', 30)

    try:
        # 创建会话以保持登录状态
        session = requests.Session()
        session.verify = False  # 忽略 HTTPS 证书错误

        # 如果需要登录，先执行登录请求
        if username and password:
            login_data = {
                'username': username,
                'password': password
            }
            login_response = session.post(f"{url}/login", json=login_data, timeout=timeout)
            login_response.raise_for_status()

        # 执行主要请求
        response = session.get(url, timeout=timeout)
        response.raise_for_status()

        # 返回响应内容（可以是 JSON、HTML 或文本）
        try:
            result = response.json()
        except:
            result = response.text

        json_ret = {"code": 0, "msg": "", "data": result}

    except requests.exceptions.Timeout:
        json_ret = {"code": -1, "msg": f"请求超时（{timeout}秒）"}
    except requests.exceptions.RequestException as e:
        json_ret = {"code": -1, "msg": f"请求失败: {str(e)}"}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

### 3. bash 命令模板

适用于执行系统命令：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""

import subprocess


def health_check(params, assets, context):
    """健康检查"""
    json_ret = {"code": 0, "msg": "", "data": ""}
    return json_ret


def execute_command(params, assets, context):
    """执行 bash 命令"""

    # 从 assets 获取配置参数
    command = assets.get('command', '')
    timeout = assets.get('timeout', 30)

    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0:
            json_ret = {"code": 0, "msg": "", "data": result.stdout.strip()}
        else:
            json_ret = {"code": -1, "msg": result.stderr.strip() or "命令执行失败"}

    except subprocess.TimeoutExpired:
        json_ret = {"code": -1, "msg": f"命令执行超时（{timeout}秒）"}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

### 4. API 调用模板

适用于调用 REST API：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""

import requests


def health_check(params, assets, context):
    """健康检查"""
    json_ret = {"code": 0, "msg": "", "data": ""}
    return json_ret


def call_api(params, assets, context):
    """调用 REST API"""

    # 从 assets 获取配置参数
    api_url = assets.get('api_url', '')
    api_key = assets.get('api_key', '')
    timeout = assets.get('timeout', 30)

    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        response = requests.get(api_url, headers=headers, timeout=timeout)
        response.raise_for_status()

        result = response.json()
        json_ret = {"code": 0, "msg": "", "data": result}

    except requests.exceptions.Timeout:
        json_ret = {"code": -1, "msg": f"API 调用超时（{timeout}秒）"}
    except requests.exceptions.RequestException as e:
        json_ret = {"code": -1, "msg": f"API 调用失败: {str(e)}"}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

## 生成代码的步骤

当用户要求生成动作代码时，请按照以下步骤：

### 1. 分析执行过程

- 识别使用了哪些工具（Web 访问、bash、API 等）
- **如果使用了 agent-browser，必须转换为 requests 库实现**
- 识别需要哪些配置参数（从执行过程中提取）
- 识别返回什么数据

### 2. 选择合适的模板

- 如果使用了 agent-browser 或需要 Web 访问 → 使用 **HTTP 请求模板**（使用 requests 库）
- 如果执行了 bash 命令 → 使用 **bash 命令模板**
- 如果调用了 API → 使用 **API 调用模板**
- 如果是其他操作 → 使用 **基础模板**

### 3. 填充模板参数

- **函数名称**：根据任务描述生成（使用下划线命名法，例如：get_system_status）
- **函数文档**：任务描述
- **资源参数**：从执行过程中提取的配置参数
- **业务逻辑**：根据执行步骤生成具体代码

### 4. 生成完整代码

- 添加必要的导入语句
- 添加 health_check 函数
- 添加主要的业务函数
- 确保代码格式正确、缩进一致

### 5. 返回代码（重要）

**不要使用 write_file 工具保存代码！** 而是直接在回复中返回 JSON 格式的代码：

```json
{
    "config": "config.json 的完整内容（JSON 字符串）",
    "main": "main.py 的完整内容（Python 代码字符串）"
}
```

**返回格式示例**：

```json
{
    "config": "{\"name\": \"服务器状态监控\", \"main\": \"main.py\", ...}",
    "main": "# coding=utf-8\n\"\"\"\nAIOE 应用动作代码\n\"\"\"\n\nimport subprocess\n..."
}
```

**注意事项**：
- 必须在回复的最后以 JSON 代码块的形式返回
- config 字段是 config.json 的完整 JSON 内容（作为字符串）
- main 字段是 main.py 的完整 Python 代码（作为字符串，保留换行符 \n）
- 在 JSON 代码块之前，可以添加简短的说明文字
- 不要使用 write_file 工具，直接返回代码内容

## 完整示例

### 示例 1：查看服务器状态

**用户需求**：帮我查看 1.1.1.1 的服务器系统状态

**执行过程**：
1. 使用 agent-browser 访问 https://1.1.1.1/status
2. 登录（用户名：admin，密码：password）
3. 获取状态信息
4. 返回状态数据

**生成的代码**（main.py）：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""

import requests


def health_check(params, assets, context):
    """健康检查"""
    json_ret = {"code": 0, "msg": "", "data": ""}
    return json_ret


def get_system_status(params, assets, context):
    """查看服务器系统状态"""

    # 从 assets 获取配置参数
    server_ip = assets.get('server_ip', '1.1.1.1')
    username = assets.get('username', 'admin')
    password = assets.get('password', '')

    try:
        # 创建会话
        session = requests.Session()
        session.verify = False  # 忽略 HTTPS 证书错误

        # 登录
        login_url = f"https://{server_ip}/login"
        login_data = {'username': username, 'password': password}
        login_response = session.post(login_url, json=login_data, timeout=30)
        login_response.raise_for_status()

        # 获取状态信息
        status_url = f"https://{server_ip}/status"
        status_response = session.get(status_url, timeout=30)
        status_response.raise_for_status()

        # 解析响应
        try:
            result = status_response.json()
        except:
            result = status_response.text

        json_ret = {"code": 0, "msg": "", "data": result}

    except requests.exceptions.Timeout:
        json_ret = {"code": -1, "msg": "请求超时"}
    except requests.exceptions.RequestException as e:
        json_ret = {"code": -1, "msg": f"请求失败: {str(e)}"}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

**配套的 config.json**：

```json
{
    "name": "服务器状态监控",
    "main": "main.py",
    "logo": "logo.ico",
    "description": "查看服务器系统状态",
    "internal": true,
    "resource": {
        "server_ip": {
            "data_type": "string",
            "description": "服务器 IP 地址",
            "required": true,
            "options": "",
            "order": 1
        },
        "username": {
            "data_type": "string",
            "description": "登录用户名",
            "required": true,
            "options": "",
            "order": 2
        },
        "password": {
            "data_type": "string",
            "description": "登录密码",
            "required": true,
            "options": "",
            "order": 3
        }
    },
    "actions": [
        {
            "name": "健康检查",
            "func": "health_check",
            "classify": "query",
            "input": []
        },
        {
            "name": "获取系统状态",
            "func": "get_system_status",
            "classify": "query",
            "input": []
        }
    ]
}
```

### 示例 2：API 调用

**用户需求**：调用 API 获取服务器信息

**执行过程**：
1. 调用 GET https://api.example.com/servers/{server_id}
2. 使用 API key 认证
3. 返回 JSON 结果

**生成的代码**（main.py）：

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""

import requests


def health_check(params, assets, context):
    """健康检查"""
    json_ret = {"code": 0, "msg": "", "data": ""}
    return json_ret


def get_server_info(params, assets, context):
    """获取服务器信息"""

    # 从 assets 获取配置参数
    server_id = assets.get('server_id', '')
    api_key = assets.get('api_key', '')

    try:
        url = f"https://api.example.com/servers/{server_id}"
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        result = response.json()
        json_ret = {"code": 0, "msg": "", "data": result}

    except requests.exceptions.Timeout:
        json_ret = {"code": -1, "msg": "API 调用超时"}
    except requests.exceptions.RequestException as e:
        json_ret = {"code": -1, "msg": f"API 调用失败: {str(e)}"}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}

    return json_ret
```

## 注意事项

1. **必须包含 health_check 函数**：每个 main.py 都应该包含一个 health_check 函数
2. **使用 try-except**：所有业务逻辑都应该在 try-except 块中
3. **从 assets 获取配置**：不要硬编码配置值，应该从 assets 获取，并提供默认值
4. **返回格式统一**：必须返回 `{"code": 0/-1, "msg": "", "data": ...}` 格式
5. **导入语句**：只导入需要的库，放在文件开头
6. **编码声明**：文件开头必须有 `# coding=utf-8`
7. **函数命名**：使用下划线命名法（snake_case），例如：get_system_status
8. **超时处理**：对于可能长时间运行的操作（网络请求、命令执行），设置合理的超时时间
9. **资源清理**：使用完资源后要清理（例如：关闭会话）
10. **错误信息**：返回的错误信息应该清晰、有用
11. **禁止使用 agent-browser**：所有 Web 访问必须使用 requests 库，不得使用 agent-browser 工具

## config.json 规范

每个应用都需要配套的 config.json 文件：

```json
{
    "name": "应用名称",
    "main": "main.py",
    "logo": "logo.ico",
    "description": "应用描述",
    "internal": true,
    "resource": {
        "资源名称": {
            "data_type": "string|number|boolean",
            "description": "资源描述",
            "required": true|false,
            "options": "选项列表（可选）",
            "order": 1
        }
    },
    "actions": [
        {
            "name": "动作名称",
            "func": "函数名",
            "classify": "query|action|mutation",
            "input": []
        }
    ]
}
```

**字段说明**：
- `name` - 应用名称
- `main` - 主文件名（固定为 "main.py"）
- `logo` - 图标文件名（固定为 "logo.ico"）
- `description` - 应用描述
- `internal` - 是否为内部应用（通常为 true）
- `resource` - 资源配置字典
  - `data_type` - 数据类型：string、number、boolean
  - `description` - 资源描述
  - `required` - 是否必填
  - `options` - 选项列表（可选）
  - `order` - 显示顺序
- `actions` - 动作列表
  - `name` - 动作名称（显示给用户）
  - `func` - 函数名（main.py 中的函数名）
  - `classify` - 分类：query（查询）、action（操作）、mutation（修改）
  - `input` - 输入参数（通常为空数组）
