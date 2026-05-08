# aioe-app-dev

AIOE 应用动作代码生成规范 - 生成符合 aioe_app 格式的 Python 代码。

## 工作流程

1. 用户提出需求 → 2. 执行并返回结果 → 3. 用户确认 → 4. 生成动作代码

## 核心规范

### 约束：禁止 agent-browser

**必须使用 `requests` 库**，禁止 `agent-browser`。转换规则：
- `agent-browser open <url>` → `requests.get/post(url)`
- 登录 → `requests.Session()` 保持会话
- 截图 → 获取 HTML/API 数据
- 页面交互 → 调用后端 API

### 文件头部

```python
# coding=utf-8
"""
AIOE 应用动作代码
"""
```

### 函数签名

```python
def function_name(params, assets, context):
    """函数文档字符串"""
    # params: 调用参数, assets: 资源配置, context: 上下文
```

### 返回格式

```python
# 成功: {"code": 0, "msg": "", "data": result_data}
# 失败: {"code": -1, "msg": "错误信息"}
```

### 异常处理

```python
def function_name(params, assets, context):
    try:
        result = do_something()
        json_ret = {"code": 0, "msg": "", "data": result}
    except Exception as e:
        json_ret = {"code": -1, "msg": str(e)}
    return json_ret
```

### 资源配置

```python
server_ip = assets.get('server_ip', '')  # 字符串
port = assets.get('port', 0)             # 数字
enabled = assets.get('enabled', False)   # 布尔
```

## 代码模板

### HTTP 请求（替代 agent-browser）

```python
import requests

def web_request(params, assets, context):
    url = assets.get('url', '')
    username = assets.get('username', '')
    password = assets.get('password', '')

    try:
        session = requests.Session()
        session.verify = False

        if username and password:
            session.post(f"{url}/login", json={'username': username, 'password': password}, timeout=30)

        response = session.get(url, timeout=30)
        response.raise_for_status()

        try:
            result = response.json()
        except:
            result = response.text

        return {"code": 0, "msg": "", "data": result}
    except requests.exceptions.Timeout:
        return {"code": -1, "msg": "请求超时"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}
```

### Bash 命令

```python
import subprocess

def execute_command(params, assets, context):
    command = assets.get('command', '')
    timeout = assets.get('timeout', 30)

    try:
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=timeout)

        if result.returncode == 0:
            return {"code": 0, "msg": "", "data": result.stdout.strip()}
        else:
            return {"code": -1, "msg": result.stderr.strip() or "命令执行失败"}
    except subprocess.TimeoutExpired:
        return {"code": -1, "msg": f"命令执行超时（{timeout}秒）"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}
```

### API 调用

```python
import requests

def call_api(params, assets, context):
    api_url = assets.get('api_url', '')
    api_key = assets.get('api_key', '')

    try:
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()

        return {"code": 0, "msg": "", "data": response.json()}
    except requests.exceptions.Timeout:
        return {"code": -1, "msg": "API 调用超时"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}
```

## 生成步骤

1. **分析执行过程**：识别工具（Web/bash/API）、配置参数、返回数据。agent-browser 必须转换为 requests
2. **选择模板**：Web 访问用 HTTP 模板，bash 用命令模板，API 用 API 模板
3. **填充参数**：函数名（snake_case）、文档、资源参数、业务逻辑
4. **生成代码**：添加导入、health_check、业务函数

## 返回格式（重要）

**直接返回 JSON，不使用 write_file**：

```json
{
    "config": "config.json 完整内容（JSON 字符串）",
    "main": "main.py 完整代码（保留 \\n）"
}
```

## 完整示例

### 查看服务器状态

**需求**：查看 1.1.1.1 服务器系统状态

**main.py**：
```python
# coding=utf-8
"""
AIOE 应用动作代码
"""
import requests

def health_check(params, assets, context):
    return {"code": 0, "msg": "", "data": ""}

def get_system_status(params, assets, context):
    """查看服务器系统状态"""
    server_ip = assets.get('server_ip', '1.1.1.1')
    username = assets.get('username', 'admin')
    password = assets.get('password', '')

    try:
        session = requests.Session()
        session.verify = False

        login_url = f"https://{server_ip}/login"
        session.post(login_url, json={'username': username, 'password': password}, timeout=30)

        status_url = f"https://{server_ip}/status"
        response = session.get(status_url, timeout=30)
        response.raise_for_status()

        try:
            result = response.json()
        except:
            result = response.text

        return {"code": 0, "msg": "", "data": result}
    except requests.exceptions.Timeout:
        return {"code": -1, "msg": "请求超时"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}
```

**config.json**：
```json
{
    "name": "服务器状态监控",
    "main": "main.py",
    "logo": "logo.ico",
    "description": "查看服务器系统状态",
    "internal": true,
    "resource": {
        "server_ip": {"data_type": "string", "description": "服务器 IP", "required": true, "order": 1},
        "username": {"data_type": "string", "description": "用户名", "required": true, "order": 2},
        "password": {"data_type": "string", "description": "密码", "required": true, "order": 3}
    },
    "actions": [
        {"name": "健康检查", "func": "health_check", "classify": "query", "input": []},
        {"name": "获取系统状态", "func": "get_system_status", "classify": "query", "input": []}
    ]
}
```

## 注意事项

1. **必须包含 health_check 函数**
2. **使用 try-except 包裹业务逻辑**
3. **从 assets 获取配置，提供默认值**
4. **返回格式统一**：`{"code": 0/-1, "msg": "", "data": ...}`
5. **文件开头必须有 `# coding=utf-8`**
6. **函数命名使用 snake_case**
7. **设置合理的超时时间**
8. **禁止使用 agent-browser，必须用 requests**

## config.json 规范

```json
{
    "name": "应用名称",
    "main": "main.py",
    "logo": "logo.ico",
    "description": "应用描述",
    "internal": true,
    "resource": {
        "资源名": {
            "data_type": "string|number|boolean",
            "description": "资源描述",
            "required": true|false,
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
