---
name: lion-mgmt
description: "通过 HTTP 调用 Lion MGMT 后台的 REST API。按优先级智能匹配解决方案：剧本 → 动作 → 底层接口"
metadata: {"nanobot":{"emoji":"🦁"}}
---

# Skill: lion-mgmt

## 🚨 重要提醒（每次执行前必读）

**智能使用历史记录提高执行效率**

本 skill 支持通过历史记录优化执行流程：

### 历史记录使用策略

**✅ 允许的行为**:
- 查询历史记录，检查相同请求的执行结果
- 如果历史记录显示**成功**，尝试快速路径（复用匹配结果和参数）
- 如果历史记录显示**失败**，直接执行完整流程
- 如果快速路径失败，自动回退到完整流程

**⚠️ 历史记录来源要求**:
- 历史记录必须来自**真实执行结果**，不能伪造示例数据当作历史记录
- 如果系统当前没有历史记录存储能力，则只能把这里的历史记录规则当作**设计约束**，不能在执行时声称“已查询到历史”
- 只有当宿主运行环境明确提供历史记录存储/查询能力时，才可以执行 Step 0 的快速路径判断

**⚠️ 关键原则**:
- Token 必须每次重新获取（不能使用历史 token）
- 作战室 ID 必须使用当前会话的 ID（不能使用历史 ID）
- 快速路径失败时，必须回退到完整流程
- 历史记录仅用于优化匹配过程，不能跳过验证

**执行模式**:
1. **快速路径**（历史记录显示成功）：登录 → 直接调用 → 成功则返回，失败则回退
2. **完整流程**（历史记录显示失败或快速路径失败）：登录 → 获取数据 → 匹配 → 调用 → 返回

## 🎯 核心执行流程

### 智能执行流程（支持快速路径）

```
0. 查询历史记录
   - 如果找到成功的历史记录 → 尝试快速路径（跳到步骤 1-快速）
   - 如果找到失败的历史记录 → 执行完整流程（跳到步骤 1-完整）
   - 如果没有历史记录 → 执行完整流程（跳到步骤 1-完整）

【快速路径】（历史记录显示成功）
1-快速. 登录获取 token
2-快速. 使用历史记录中的匹配结果和参数，直接调用接口
3-快速. 如果调用成功 → 跳到步骤 11
4-快速. 如果调用失败（401/404/400等）→ 回退到完整流程（步骤 1-完整）

【完整流程】（历史记录显示失败或快速路径失败）
1-完整. 登录获取 token
2-完整. 调用接口获取 openapi.json（所有接口信息）
3-完整. 调用接口获取 playbook 列表
4-完整. 匹配 playbook，匹配成功则直接从第10步继续执行
5-完整. 调用接口获取 action 列表
6-完整. 匹配 action，匹配成功则直接从第9步继续执行
7-完整. 匹配 openapi，匹配成功则直接从第10步继续执行
8-完整. 使用 AI 进行推理
9-完整. 如果匹配到 action，查询资源 ID（使用资源 ID，不是资源名称）
10-完整. 调用对应接口（使用当前会话的作战室 ID）
11. 处理响应：
   - playbook/action: 提取 run_id，查询执行结果并返回
   - openapi: 直接返回业务数据
12. 保存执行记录到历史（包含请求、匹配结果、参数、执行状态）
13. 返回结果给用户
```

## 📋 执行清单（Checklist）

每次调用时，按顺序勾选：

- [ ] **Step 0**: 查询历史记录
  - 检查是否有相同请求的历史记录
  - 如果有成功记录 → 尝试快速路径
  - 如果有失败记录或无记录 → 执行完整流程

### 快速路径清单（历史记录显示成功）
- [ ] **Step 1-快速**: 调用 `/user/local-auth` 获取新的 token（⚠️ 必须执行）
- [ ] **Step 2-快速**: 使用历史记录中的匹配结果（playbook/action/openapi）
- [ ] **Step 3-快速**: 使用历史记录中的参数，但替换为当前会话的作战室 ID 和新 token
- [ ] **Step 4-快速**: 调用对应接口
  - 成功 → 跳到 Step 11
  - 失败（401/404/400等）→ 回退到完整流程

### 完整流程清单（历史记录显示失败或快速路径失败）
- [ ] **Step 1-完整**: 调用 `/user/local-auth` 获取新的 token（⚠️ 每次必须执行）
- [ ] **Step 2-完整**: 调用 `GET /docs/openapi.json` 获取所有接口信息
- [ ] **Step 3-完整**: 调用 `POST /playbook/published-versions` 获取剧本列表
- [ ] **Step 4-完整**: 尝试匹配 playbook
  - 匹配成功 → 跳到 Step 10-完整
  - 匹配失败 → 继续 Step 5-完整
- [ ] **Step 5-完整**: 调用 `POST /tools/actions/available` 获取动作列表
- [ ] **Step 6-完整**: 尝试匹配 action
  - 匹配成功 → 继续 Step 9-完整
  - 匹配失败 → 继续 Step 7-完整
- [ ] **Step 7-完整**: 尝试匹配 openapi
  - 匹配成功 → 跳到 Step 10-完整
  - 匹配失败 → 继续 Step 8-完整
- [ ] **Step 8-完整**: 使用 AI 进行推理（组合多个接口或提供建议）
- [ ] **Step 9-完整**: 如果匹配到 action，查询资源 ID（POST `/tools/resource/detailed`）
- [ ] **Step 10-完整**: 调用对应接口（使用当前会话的作战室 ID）

### 通用步骤（快速路径和完整流程都需要）
- [ ] **Step 11**: 处理响应（playbook/action 提取 run_id 并查询结果，openapi 提取业务数据）
- [ ] **Step 12**: 保存执行记录到历史（包含请求、匹配结果、参数、执行状态）
- [ ] **Step 13**: 返回结果给用户

## 📚 Step 0: 历史记录查询与格式

### 历史记录查询

在执行任何操作之前，可以先查询历史记录，检查是否有相同请求的执行记录。

**前提条件**:
- 只有当运行环境明确提供历史记录存储/查询能力时，才能执行这一步
- 如果没有历史记录能力，则直接跳过 Step 0，进入完整流程
- 不要把文档中的示例 JSON 当作真实可读的历史数据源

**查询条件**:
- 用户请求的语义相似度（例如："查询 IP 1.1.1.1" 和 "查询 IP 1.1.1.1 的威胁情报" 应视为相同请求）
- 请求参数相同（例如：IP 地址、域名、哈希值等）

**历史记录格式**:

```json
{
  "request": {
    "user_query": "查询 IP 1.1.1.1 的威胁情报",
    "parameters": {
      "ip": "1.1.1.1"
    }
  },
  "match_result": {
    "type": "action",  // playbook | action | openapi
    "matched_item": {
      // 如果是 action
      "node_name": "微步在线",
      "action": "ip_query",
      "action_name": "IP分析，目前支持单个查询",
      "app_name": "threatbook",
      "name": "微步在线",
      "app_ver": "6d86a27ac2f7be76154e986c05f13994daff0a60",
      "main": "main.py",
      "resource_name": "微步在线-个人"
    }
  },
  "execution": {
    "status": "success",  // success | failed
    "error_code": null,   // 如果失败，记录错误码（401/404/400等）
    "error_message": null,
    "timestamp": "2026-05-02T02:00:00Z"
  }
}
```

### 历史记录使用规则

**如果历史记录显示成功** (`execution.status == "success"`):
1. 提取 `match_result` 中的匹配信息
2. 提取 `matched_item` 中的参数
3. 执行快速路径：
   - 登录获取新 token
   - 如果是 action，查询资源 ID（使用 `resource_name`）
   - 使用历史参数构造请求，但替换为当前会话的作战室 ID 和新 token
   - 调用接口
4. 如果调用成功，返回结果并更新历史记录
5. 如果调用失败（401/404/400等），回退到完整流程

**如果历史记录显示失败** (`execution.status == "failed"`):
1. 直接执行完整流程
2. 不尝试快速路径

**如果没有历史记录**:
1. 直接执行完整流程

### 历史记录保存

在执行完成后（Step 12），如果宿主环境支持历史记录持久化，则保存执行记录到历史：

```python
history_record = {
    "request": {
        "user_query": "<用户原始请求>",
        "parameters": {
            # 从用户请求中提取的参数
        }
    },
    "match_result": {
        "type": "<playbook|action|openapi>",
        "matched_item": {
            # 匹配到的项目的完整信息
        }
    },
    "execution": {
        "status": "success" if success else "failed",
        "error_code": error_code if failed else None,
        "error_message": error_message if failed else None,
        "timestamp": datetime.now().isoformat()
    }
}
# 保存到历史记录存储（可以是文件、数据库或内存）
```

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

## 📂 Step 2-5: 获取数据（完整流程）

**⚠️ 适用场景**: 仅在完整流程中执行，快速路径跳过此步骤。

**强制要求**: 在完整流程中，必须调用接口获取最新数据。

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

**执行规则**:
- ✅ 完整流程中，必须调用接口获取最新数据
- ✅ 快速路径中，跳过此步骤，直接使用历史记录中的匹配结果
- ❌ 完整流程中，不要跳过调用接口

## 🎯 Step 4-8: 匹配逻辑（完整流程）

**⚠️ 适用场景**: 仅在完整流程中执行，快速路径跳过此步骤。

**执行规则**:
- ✅ 完整流程中，必须重新从接口获取的数据中查找匹配
- ✅ 快速路径中，直接使用历史记录中的匹配结果
- ✅ 即使历史记录显示失败，完整流程中也要重新尝试匹配
- ❌ 完整流程中，不要因为历史记录的失败而跳过匹配尝试

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

## 📤 Step 11-13: 处理响应、保存历史并返回结果

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

### Step 12: 保存历史记录

在执行完成后（无论成功还是失败），如果宿主环境支持历史记录持久化，则保存执行记录到历史，以便后续执行时可以使用快速路径。

**保存内容**:
```python
history_record = {
    "request": {
        "user_query": "<用户原始请求>",
        "parameters": {
            # 从用户请求中提取的参数
            # 例如: {"ip": "1.1.1.1"} 或 {"domain": "example.com"}
        }
    },
    "match_result": {
        "type": "<playbook|action|openapi>",
        "matched_item": {
            # 匹配到的项目的完整信息
            # 如果是 action，包含: node_name, action, action_name, app_name, name, app_ver, main, resource_name
            # 如果是 playbook，包含: flow_id, name, description
            # 如果是 openapi，包含: path, method, operationId
        }
    },
    "execution": {
        "status": "success" if success else "failed",
        "error_code": error_code if failed else None,  # 401/404/400/500等
        "error_message": error_message if failed else None,
        "timestamp": datetime.now().isoformat()
    }
}
# 保存到历史记录存储（可以是文件、数据库或内存）
```

**保存时机**:
- ✅ 快速路径成功：保存成功记录
- ✅ 快速路径失败，完整流程成功：保存成功记录（更新匹配结果）
- ✅ 完整流程失败：保存失败记录（包含错误信息）

**保存位置**:
- 建议保存在会话级别的存储中（例如：Redis、文件、数据库）
- 键值格式：`history:<user_id>:<request_hash>` → `history_record`
- 如果当前 skill 运行环境没有读写存储的能力，则不要虚构历史记录读写结果

### Step 13: 返回结果给用户

将执行结果返回给用户，格式参考上面的"返回格式示例"。

## ⚠️ 关键规则

1. **历史记录使用规则**（重要）:
   - ✅ 允许查询历史记录优化执行流程
   - ✅ 如果历史记录显示成功，可以尝试快速路径
   - ✅ 如果快速路径失败，必须回退到完整流程
   - ✅ 如果环境不支持历史记录查询，则直接走完整流程
   - ❌ 不要因为历史记录显示失败就放弃尝试，必须执行完整流程
   - ❌ 不要跳过验证步骤（token、作战室 ID、资源 ID 都必须重新获取）
   - ❌ 不要把示例数据或模型记忆当作真实历史记录

2. **禁止假设 token 有效**（重要）: 每次执行 skill 时，必须先调用登录接口获取新的 token
   - ✅ 每次都重新调用 `/user/local-auth` 获取 token（快速路径和完整流程都需要）
   - ❌ 不要使用缓存的 token 或历史记忆中的 token
   - ❌ 不要假设 token 仍然有效
   - Token 可能会过期，使用过期的 token 会导致 401 错误

3. **严格顺序**（完整流程）: 必须先尝试 playbook，失败后才尝试 action，再失败才尝试 openapi

4. **作战室 ID 获取**（重要）:
   - ✅ 从当前会话上下文中获取作战室 ID
   - ❌ 不要查询作战室列表 (`/warroom/list`)
   - ❌ 不要使用历史记忆中的旧作战室 ID
   - ⚠️ 快速路径中，必须将历史记录中的作战室 ID 替换为当前会话的作战室 ID

5. **资源 ID 获取**（重要）:
   - ✅ 调用 `POST /tools/resource/detailed` 获取资源 ID
   - ✅ 使用响应中的 `data[0].id` 字段
   - ❌ 不要使用资源名称（如"微步在线-个人"）
   - ⚠️ 快速路径中，可以使用历史记录中的资源名称，但必须重新查询资源 ID

6. **区分响应类型**:
   - Playbook/Action: 必须从响应中提取 `run_id`，并通过 run_id 查询执行结果，返回给用户
   - OpenAPI: 直接返回业务数据，无需提取 run_id

7. **快速路径回退机制**（重要）:
   - ✅ 如果快速路径调用失败（401/404/400/500等），必须立即回退到完整流程
   - ✅ 回退时，从 Step 2-完整开始（已经有 token 了）
   - ✅ 回退后，必须重新获取数据、重新匹配、重新调用
   - ❌ 不要在快速路径失败后直接返回错误给用户

8. **历史记录保存**（重要）:
   - ✅ 每次执行完成后，必须保存执行记录到历史
   - ✅ 记录必须包含：用户请求、匹配结果、参数、执行状态、错误信息（如果失败）
   - ✅ 成功和失败的记录都要保存

## 🐛 错误处理

| 错误 | 处理方式 |
|------|----------|
| 401 Unauthorized | 重新执行 Step 1 登录 |
| 400 Bad Request | 检查参数是否满足 `input_schema` |
| 404 Not Found | 检查接口路径是否正确 |
| 500 Server Error | 返回错误信息给用户 |

## 📝 示例对话

### 示例 1：快速路径（历史记录显示成功）

**用户**: "查询 IP 1.1.1.1 的威胁情报"

**执行流程**:
1. ✅ Step 0: 查询历史记录 → 找到成功的历史记录
2. ✅ Step 1-快速: 调用 `/user/local-auth` 获取新 token
3. ✅ Step 2-快速: 从历史记录提取匹配结果（action: "IP分析"）
4. ✅ Step 3-快速: 从历史记录提取参数，替换为当前作战室 ID 和新 token
5. ✅ Step 4-快速: 调用 `POST /tools/resource/detailed` 查询资源 ID
6. ✅ Step 4-快速: 调用 `POST /warroom/run` 执行动作 → 成功
7. ✅ Step 11: 获取响应并提取 `run_id: "run_12345"`
8. ✅ Step 11: 调用 `GET /warroom/run/run_12345` 查询执行结果
9. ✅ Step 12: 更新历史记录（标记为成功）
10. ✅ Step 13: 返回给用户:
    ```
    ✅ 已提交 IP 威胁情报查询任务（使用快速路径）
    执行 ID: run_12345
    状态: 已完成
    结果: 1.1.1.1 不危险
    ```

### 示例 2：完整流程（历史记录显示失败或无历史记录）

**用户**: "查询域名 example.com 的威胁情报"

**执行流程**:
1. ✅ Step 0: 查询历史记录 → 未找到历史记录
2. ✅ Step 1-完整: 调用 `/user/local-auth` 获取 token
3. ✅ Step 2-完整: 调用 `GET /docs/openapi.json` 获取接口信息
4. ✅ Step 3-完整: 调用 `POST /playbook/published-versions` 获取剧本列表
5. ✅ Step 4-完整: 匹配 playbook → 未找到匹配
6. ✅ Step 5-完整: 调用 `POST /tools/actions/available` 获取动作列表
7. ✅ Step 6-完整: 匹配 action → 找到 "域名分析" 动作
8. ✅ Step 9-完整: 调用 `POST /tools/resource/detailed` 查询资源 ID
9. ✅ Step 10-完整: 调用 `POST /warroom/run` 执行动作
10. ✅ Step 11: 获取响应并提取 `run_id: "run_67890"`
11. ✅ Step 11: 调用 `GET /warroom/run/run_67890` 查询执行结果
12. ✅ Step 12: 保存执行记录到历史（标记为成功）
13. ✅ Step 13: 返回给用户:
    ```
    ✅ 已提交域名威胁情报查询任务
    执行 ID: run_67890
    状态: 已完成
    结果: example.com 安全
    ```

### 示例 3：快速路径失败，回退到完整流程

**用户**: "查询 IP 2.2.2.2 的威胁情报"

**执行流程**:
1. ✅ Step 0: 查询历史记录 → 找到成功的历史记录（之前查询过 IP 1.1.1.1）
2. ✅ Step 1-快速: 调用 `/user/local-auth` 获取新 token
3. ✅ Step 2-快速: 从历史记录提取匹配结果（action: "IP分析"）
4. ✅ Step 3-快速: 从历史记录提取参数，替换为当前作战室 ID 和新 token
5. ✅ Step 4-快速: 调用 `POST /tools/resource/detailed` 查询资源 ID
6. ✅ Step 4-快速: 调用 `POST /warroom/run` 执行动作 → 失败（404 资源不存在）
7. ⚠️ 快速路径失败，回退到完整流程
8. ✅ Step 2-完整: 调用 `GET /docs/openapi.json` 获取接口信息
9. ✅ Step 3-完整: 调用 `POST /playbook/published-versions` 获取剧本列表
10. ✅ Step 4-完整: 匹配 playbook → 未找到匹配
11. ✅ Step 5-完整: 调用 `POST /tools/actions/available` 获取动作列表
12. ✅ Step 6-完整: 匹配 action → 找到 "IP分析" 动作（使用不同的资源）
13. ✅ Step 9-完整: 调用 `POST /tools/resource/detailed` 查询资源 ID
14. ✅ Step 10-完整: 调用 `POST /warroom/run` 执行动作 → 成功
15. ✅ Step 11: 获取响应并提取 `run_id: "run_11111"`
16. ✅ Step 11: 调用 `GET /warroom/run/run_11111` 查询执行结果
17. ✅ Step 12: 保存执行记录到历史（标记为成功，更新资源信息）
18. ✅ Step 13: 返回给用户:
    ```
    ✅ 已提交 IP 威胁情报查询任务
    执行 ID: run_11111
    状态: 已完成
    结果: 2.2.2.2 不危险
    ```

## 配置说明

本 skill 使用硬编码配置，无需设置环境变量：

- **服务地址**: `http://127.0.0.1:8000`（固定）
- **登录用户名**: `admin`（固定）
- **认证方式**: 通过 `/user/local-auth` 接口获取 token（仅限本地访问）
