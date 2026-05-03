---
name: lion-mgmt
description: "通过 HTTP 调用 Lion MGMT 后台的 REST API。按优先级智能匹配解决方案：剧本 → 动作 → 底层接口"
metadata: {"nanobot":{"emoji":"🦁"}}
---

# Skill: lion-mgmt

## 🚨 重要提醒（每次执行前必读）


**智能使用历史记录提高执行效率**

- ❌ 不要根据记忆中的"资源不可用"跳过执行
- ❌ 不要根据记忆中的"上次失败"跳过尝试
- ❌ 不要根据记忆中的"接口数据"跳过调用
- ❌ 不要根据记忆中的"匹配结果"跳过匹配
- ✅ 每次都必须完整执行所有步骤
- ✅ 每次都必须重新调用接口获取最新数据
- ✅ 每次都必须重新匹配
- ✅ 每次都必须重新调用业务接口

**注意**：如果历史记录中存在执行成功的记录，则根据记录直接调用业务接口

**原因**: 资源、数据、配置随时可能更新，历史记忆不可靠。

## 🎯 核心执行流程（每次必须完整执行）

```
1. 登录获取 token
2. 调用接口获取 openapi.json（所有接口信息）
3. 调用接口获取 playbook 列表
4. 匹配 playbook，匹配成功则直接从第10步继续执行
5. 调用接口获取 action 列表
6. 匹配 action，匹配成功则直接从第9步继续执行
7. 匹配 openapi，匹配成功则直接从第10步继续执行
8. 使用 AI 进行推理
9. 如果匹配到 action，查询资源 ID（使用资源 ID，不是资源名称）
10. 调用对应接口（使用当前会话的作战室 ID）
11. 处理响应：
   - playbook/action: 提取 run_id，查询执行结果并返回
   - openapi: 直接返回业务数据
12. 返回结果给用户
```

## 📋 执行清单（Checklist）

每次调用时，按顺序勾选：

- [ ] **Step 1**: 调用 `/user/local-auth` 获取新的 token（⚠️ 每次必须执行，不要使用缓存的 token）
- [ ] **Step 2**: 调用 `GET /docs/openapi.json` 获取所有接口信息
- [ ] **Step 3**: 调用 `POST /playbook/published-versions` 获取剧本列表
- [ ] **Step 4**: 尝试匹配 playbook
  - 匹配成功 → 跳到 Step 10
  - 匹配失败 → 继续 Step 5
- [ ] **Step 5**: 调用 `POST /tools/actions/available` 获取动作列表
- [ ] **Step 6**: 尝试匹配 action
  - 匹配成功 → 继续 Step 9
  - 匹配失败 → 继续 Step 7
- [ ] **Step 7**: 尝试匹配 openapi
  - 匹配成功 → 跳到 Step 10
  - 匹配失败 → 继续 Step 8
- [ ] **Step 8**: 使用 AI 进行推理（组合多个接口或提供建议）
- [ ] **Step 9**: 如果匹配到 action，查询资源 ID（POST `/tools/resource/detailed`）
- [ ] **Step 10**: 调用对应接口（使用当前会话的作战室 ID）
- [ ] **Step 11**: 处理响应（playbook/action 提取 run_id 并查询结果，openapi 提取业务数据）
- [ ] **Step 12**: 返回结果给用户

## 🔐 Step 1: 登录

**⚠️ 强制要求**: 每次执行 skill 时，必须先调用登录接口获取新的 token。

**禁止行为**:
- ❌ 不要使用缓存的 token
- ❌ 不要使用历史记忆中的 token
- ❌ 不要假设 token 仍然有效
- ✅ 必须每次都重新调用登录接口获取新的 token

**原因**: Token 可能会过期，使用过期的 token 会导致请求失败（401 Unauthorized）。

```python
import os
import requests

base_url = os.getenv("LION_MGMT_BASE_URL", "http://127.0.0.1:8000")
username = os.getenv("LION_MGMT_USERNAME", "admin")

response = requests.post(
    f"{base_url}/user/local-auth",
    headers={"Content-Type": "application/json"},
    json={"username": username}
)
data = response.json()
token = data["data"]["token"]
print(f"Token: {token}")
```

**响应**: 返回 token（格式：`account_id:jwt_token`），后续所有请求必须在 HTTP 请求头中添加 `aioe-auth: <token>`（将 `<token>` 替换为实际的 token 值）

## 📂 Step 2-5: 获取数据

**⚠️ 强制要求**: 每次都必须调用接口获取最新数据，禁止使用记忆或缓存中的数据。

依次调用以下接口获取数据：

### Step 2: 获取 OpenAPI 接口信息

```python
import requests

token = "<token>"  # 从 Step 1 获取的 token

response = requests.get(
    "http://127.0.0.1:8000/docs/openapi.json",
    headers={"aioe-auth": token}
)
openapi_data = response.json()
# 提取 paths 字段
paths = openapi_data.get("paths", {})
```

### Step 3: 获取 Playbook 列表

```python
response = requests.post(
    "http://127.0.0.1:8000/playbook/published-versions",
    headers={"aioe-auth": token}
)
playbook_data = response.json()
# 提取 data.items 字段
playbooks = playbook_data.get("data", {}).get("items", [])
```

### Step 5: 获取 Action 列表

```python
response = requests.post(
    "http://127.0.0.1:8000/tools/actions/available",
    headers={"aioe-auth": token}
)
action_data = response.json()
# 提取 data.items 字段
actions = action_data.get("data", {}).get("items", [])
```

**禁止行为**:
- ❌ 不要说"我记得上次的数据是..."
- ❌ 不要说"根据之前的数据..."
- ❌ 不要跳过调用接口
- ✅ 必须每次都重新调用接口获取最新数据

## 🎯 Step 4-8: 匹配逻辑

**⚠️ 强制要求**: 每次都必须重新匹配，禁止使用记忆中的历史匹配结果。

**禁止行为**:
- ❌ 不要说"我记得上次这个资源不可用"
- ❌ 不要说"根据之前的匹配结果，这个动作不存在"
- ❌ 不要因为记忆中的失败而跳过尝试
- ✅ 必须每次都重新从接口获取的数据中查找匹配
- ✅ 即使记忆中显示"资源不可用"，也要重新尝试

### 优先级 1: Playbook（剧本）- Step 4

**匹配字段**: `name`, `description`, `tags`

**匹配成功后**: 直接跳到 **Step 10**（调用对应接口）

**⚠️ 关键参数说明**:
1. **作战室 ID (`id`)**: 
   - 从当前会话上下文中获取（通常在 AI 消息的 metadata 中）
   - ❌ 不要查询作战室列表
   - ❌ 不要使用历史记忆中的旧 ID
   - ✅ 使用当前会话提供的作战室 ID

**调用接口**:

```http
POST ${LION_MGMT_BASE_URL}/warroom/run
aioe-auth: <token>
Content-Type: application/json

{
  "id": "<当前会话的作战室ID>",
  "type": "flow",
  "playbook_input": {
    "flow_id": "<从 playbook 中提取，这是剧本的版本id>",
    "flow_input": {
      // 从用户对话中提取的参数
    }
  }
}
```

**响应结构**:

```json
{
    "code": 0,
    "data": {
        "run_id": "202604141016540891"
    }
}
```

**必须提取**: `run_id` 字段

### 优先级 2: Action（动作）- Step 6

**匹配字段**: `name`, `description`, `tags`

**匹配成功后**: 继续 **Step 9**（查询资源 ID），然后执行 **Step 10**（调用对应接口）

**⚠️ 关键参数说明**:
1. **作战室 ID (`id`)**: 
   - 从当前会话上下文中获取（通常在 AI 消息的 metadata 中）
   - ❌ 不要查询作战室列表
   - ❌ 不要使用历史记忆中的旧 ID
   - ✅ 使用当前会话提供的作战室 ID

2. **资源 ID (`resource`)**:
   - ❌ 不要使用资源名称（如"微步在线-个人"）
   - ✅ 必须使用资源 ID（如"7b07b298-8afa-4384-ae36-8b131aaf06bb"）
   - 获取方式: 调用 `POST /tools/resource/detailed` 接口，传入 `{"name": "工具名称"}`，从响应的 `data[0].id` 中提取

**调用接口**:
```http
POST ${LION_MGMT_BASE_URL}/warroom/run
aioe-auth: <token>
Content-Type: application/json

{
  "id": "<当前会话的作战室ID>",
  "type": "action",
  "action_input": {
    "resource": "<资源ID，不是资源名称>",
    "node_name": "<从 action 中提取>",
    "action": "<从 action 中提取>",
    "action_name": "<从 action 中提取>",
    "app_name": "<从 action 中提取>",
    "name": "<从 action 中提取>",
    "app_ver": "<从 action 中提取>",
    "main": "<从 action 中提取>",
    "action_input": {
      // 从用户对话中提取的参数
    },
    "timeout": 300
  }
}
```

**完整示例**:
```json
{
  "id": "00b2f8cc-4faf-44fc-9809-294f7303d058",
  "type": "action",
  "action_input": {
    "resource": "7b07b298-8afa-4384-ae36-8b131aaf06bb",
    "node_name": "微步在线",
    "action": "ip_query",
    "action_name": "IP分析，目前支持单个查询",
    "app_name": "threatbook",
    "name": "微步在线",
    "app_ver": "6d86a27ac2f7be76154e986c05f13994daff0a60",
    "main": "main.py",
    "action_input": {
      "ip": "9.9.9.9"
    },
    "timeout": 300
  }
}
```

**响应结构**:
```json
{
    "code": 0,
    "data": {
        "run_id": "202604141016540891"
    }
}
```

**必须提取**: `run_id`字段

### 优先级 3: OpenAPI（底层接口）- Step 7

**匹配字段**: `summary`, `description`, `operationId`

**匹配成功后**: 直接跳到 **Step 10**（调用对应接口）

**调用接口**: 根据 OpenAPI 定义构造 HTTP 请求

### 优先级 4: AI 推理 - Step 8

**触发条件**: 前 3 个优先级都未匹配成功

**处理方式**:
- 分析用户需求
- 组合多个接口调用
- 提供解决方案建议
- 或告知用户当前系统无法直接满足需求

## 📤 Step 11-12: 处理响应并返回结果

### Playbook/Action 响应处理（有 run_id）

**适用场景**: 调用 `/warroom/run` 接口执行剧本或动作

1. **提取关键字段**（必须）:
   - `run_id`: 执行任务的唯一标识符
   - `status`: 执行状态（running/completed/failed）
   - `playbook_id` 或 `action_id`: 执行的剧本或动作 ID
   - `created_at`: 创建时间

2. **返回给用户的信息**（必须包含）:
   ```
   ✅ 任务已提交
   📋 执行 ID: run_12345
   📊 状态: running
   🕐 创建时间: 2026-04-07T02:00:00Z
   ```

3. **查询执行结果**（可选）:
   如果用户需要查询执行结果，使用以下接口：
   ```http
   GET ${LION_MGMT_BASE_URL}/warroom/run/{run_id}
   aioe-auth: <token>
   ```

### OpenAPI 接口响应处理（无 run_id）

**适用场景**: 直接调用底层 REST API 接口

1. **同步返回**: OpenAPI 接口通常是同步的，直接返回业务数据
2. **返回格式**: 根据接口定义返回相应的业务数据
3. **示例**:
   ```
   ✅ 查询成功
   
   结果: [业务数据内容]
   ```

### 返回格式示例

**Playbook/Action 简洁版**（推荐）:
```
✅ 已提交 IP 查询任务
执行 ID: run_12345
状态: 运行中
```

**Playbook/Action 详细版**:
```
✅ 任务提交成功

📋 执行信息:
- 执行 ID: run_12345
- 任务类型: IP 威胁情报查询
- 状态: running
- 创建时间: 2026-04-07 10:00:00

💡 提示: 使用执行 ID 可以查询任务进度和结果
```

**OpenAPI 接口返回**:
```
✅ 查询成功

[直接展示业务数据]
```

## ⚠️ 关键规则

1. **禁止跳过步骤**: 即使记忆中有历史记录，也必须重新执行所有步骤
2. **禁止假设 token 有效**（重要）: 每次执行 skill 时，必须先调用登录接口获取新的 token
   - ✅ 每次都重新调用 `/user/local-auth` 获取 token
   - ❌ 不要使用缓存的 token 或历史记忆中的 token
   - ❌ 不要假设 token 仍然有效
   - ❌ 不要假设数据未变
   - Token 可能会过期，使用过期的 token 会导致 401 错误
3. **严格顺序**: 必须先尝试 playbook，失败后才尝试 action，再失败才尝试 openapi
4. **作战室 ID 获取**（重要）:
   - ✅ 从当前会话上下文中获取作战室 ID
   - ❌ 不要查询作战室列表 (`/warroom/list`)
   - ❌ 不要使用历史记忆中的旧作战室 ID
5. **资源 ID 获取**（重要）:
   - ✅ 调用 `POST /tools/resource/detailed` 获取资源 ID
   - ✅ 使用响应中的 `data[0].id` 字段
   - ❌ 不要使用资源名称（如"微步在线-个人"）
6. **区分响应类型**:
   - Playbook/Action: 必须从响应中提取 `run_id`，并通过 run_id 查询执行结果，返回给用户
   - OpenAPI: 直接返回业务数据，无需提取 run_id
7. **禁止使用记忆中的资源状态**（重要）:
   - ❌ 禁止说"我记得上次这个资源不可用，所以跳过"
   - ❌ 禁止说"根据历史记录，这个动作不存在"
   - ❌ 禁止说"之前尝试过失败了，所以不再尝试"
   - ✅ 必须每次都重新调用接口、重新匹配、重新尝试
   - ✅ 资源、配置、数据随时可能更新，历史记忆不可靠

## 🐛 错误处理

| 错误 | 处理方式 |
|------|----------|
| 401 Unauthorized | 重新执行 Step 1 登录 |
| 400 Bad Request | 检查参数是否满足 `input_schema` |
| 404 Not Found | 检查接口路径是否正确 |
| 500 Server Error | 返回错误信息给用户 |

## 📝 示例对话

**用户**: "查询 IP 1.1.1.1 的威胁情报"

**执行流程**:
1. ✅ Step 1: 调用 `/user/local-auth` 获取 token
2. ✅ Step 2: 调用 `GET /docs/openapi.json` 获取接口信息
3. ✅ Step 3: 调用 `POST /playbook/published-versions` 获取剧本列表
4. ✅ Step 4: 匹配 playbook → 未找到匹配
5. ✅ Step 5: 调用 `POST /tools/actions/available` 获取动作列表
6. ✅ Step 6: 匹配 action → 找到 "IP分析" 动作
7. ✅ Step 9: 调用 `POST /tools/resource/detailed` 查询资源 ID
8. ✅ Step 10: 调用 `POST /warroom/run` 执行动作
9. ✅ Step 11: 获取响应并提取 `run_id: "run_12345"`
10. ✅ Step 11: 调用 `GET /warroom/run/run_12345` 查询执行结果
11. ✅ Step 12: 返回给用户:
    ```
    ✅ 已提交 IP 威胁情报查询任务
    执行 ID: run_12345
    状态: 已完成
    结果: 1.1.1.1 不危险
    ```

## 配置说明

本 skill 使用硬编码配置，无需设置环境变量：

- **服务地址**: `http://127.0.0.1:8000`（固定）
- **登录用户名**: `admin`（固定）
- **认证方式**: 通过 `/user/local-auth` 接口获取 token（仅限本地访问）
