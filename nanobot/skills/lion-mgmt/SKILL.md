---
name: lion-mgmt
description: "通过 HTTP 调用 Lion MGMT 后台的 REST API。按优先级智能匹配解决方案：剧本 → 动作 → 底层接口"
metadata: {"nanobot":{"emoji":"🦁"}}
---

# Skill: lion-mgmt

## 🚨 重要提醒（每次执行前必读）

**禁止使用任何历史记忆或缓存信息**

- ❌ 不要根据记忆中的"资源不可用"跳过执行
- ❌ 不要根据记忆中的"上次失败"跳过尝试
- ❌ 不要根据记忆中的"文件内容"跳过读取
- ❌ 不要根据记忆中的"匹配结果"跳过匹配
- ✅ 每次都必须完整执行所有步骤
- ✅ 每次都必须重新读取文件
- ✅ 每次都必须重新匹配
- ✅ 每次都必须重新调用接口

**原因**: 资源、数据、配置随时可能更新，历史记忆不可靠。

## 🎯 核心执行流程（每次必须完整执行）

```
0. 检查数据文件时效性，如超过 24 小时则更新
1. 登录获取 cookie
2. 读取 3 个本地 JSON 文件
3. 按优先级匹配：playbook → action → openapi → AI 推理
4. 如果匹配到 action，查询资源 ID（使用资源 ID，不是资源名称）
5. 调用对应接口（使用当前会话的作战室 ID）
6. 处理响应：
   - playbook/action: 提取 run_id，查询执行结果并返回
   - openapi: 直接返回业务数据
7. 返回结果给用户
```

## 📋 执行清单（Checklist）

每次调用时，按顺序勾选：

- [ ] **Step 0**: 检查数据文件时效性（如超过 24 小时则更新）
- [ ] **Step 1**: POST `/user/login` 获取 `aioe-auth` cookie
- [ ] **Step 2**: 读取 `nanobot/skills/lion-mgmt/scripts/playbook.json`
- [ ] **Step 3**: 读取 `nanobot/skills/lion-mgmt/scripts/actions.json`
- [ ] **Step 4**: 读取 `nanobot/skills/lion-mgmt/scripts/openapi.json`
- [ ] **Step 5**: 分析用户需求，按优先级匹配
- [ ] **Step 6**: 如果匹配到 action，查询资源 ID（POST `/tools/resource/detailed`）
- [ ] **Step 7**: 调用对应接口（使用当前会话的作战室 ID）
- [ ] **Step 8**: 处理响应（playbook/action 提取 run_id 并查询结果，openapi 提取业务数据）
- [ ] **Step 9**: 返回结果给用户

## 🔄 Step 0: 检查并更新数据文件

**⚠️ 强制要求**: 每次执行 skill 时，必须先检查数据文件的时效性。

### 检查逻辑

检查以下三个文件的修改时间：
1. `nanobot/skills/lion-mgmt/scripts/playbook.json`
2. `nanobot/skills/lion-mgmt/scripts/actions.json`
3. `nanobot/skills/lion-mgmt/scripts/openapi.json`

**判断标准**: 如果任一文件的修改时间距离现在超过 24 小时（86400 秒），则需要更新所有文件。

### 检查文件时间的方法

使用 bash 命令检查文件修改时间：

```bash
# 获取文件的修改时间戳（秒）
stat -c %Y nanobot/skills/lion-mgmt/scripts/playbook.json

# 获取当前时间戳（秒）
date +%s

# 计算时间差
current_time=$(date +%s)
file_time=$(stat -c %Y nanobot/skills/lion-mgmt/scripts/playbook.json)
time_diff=$((current_time - file_time))

# 判断是否超过 24 小时（86400 秒）
if [ $time_diff -gt 86400 ]; then
  echo "需要更新文件"
else
  echo "文件仍然有效"
fi
```

**注意**: Windows 环境下使用 Git Bash，stat 命令语法相同。

### 更新文件的步骤

如果文件需要更新，按以下步骤执行：

#### 1. 登录获取 token

```bash
curl -X POST "http://127.0.0.1:8000/user/local-auth" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin"}'
```

**响应示例**:
```json
{
  "code": 0,
  "data": {
    "token": "account_id:jwt_token"
  }
}
```

**提取 token**: 从响应的 `data.token` 字段中提取，后续请求使用 Cookie: `aioe-auth=<token>`

#### 2. 更新 playbook.json

```bash
curl -X POST "http://127.0.0.1:8000/playbook/published-versions" \
  -H "Cookie: aioe-auth=<token>" \
  -o nanobot/skills/lion-mgmt/scripts/playbook.json
```

#### 3. 更新 actions.json

```bash
curl -X POST "http://127.0.0.1:8000/tools/actions/available" \
  -H "Cookie: aioe-auth=<token>" \
  -o nanobot/skills/lion-mgmt/scripts/actions.json
```

#### 4. 更新 openapi.json

```bash
curl -X GET "http://127.0.0.1:8000/docs/openapi.json" \
  -H "Cookie: aioe-auth=<token>" \
  -o nanobot/skills/lion-mgmt/scripts/openapi.json
```

### 完整示例

```bash
# 1. 检查文件时间
current_time=$(date +%s)
file_time=$(stat -c %Y nanobot/skills/lion-mgmt/scripts/playbook.json 2>/dev/null || echo 0)
time_diff=$((current_time - file_time))

# 2. 如果超过 24 小时或文件不存在，则更新
if [ $time_diff -gt 86400 ] || [ ! -f nanobot/skills/lion-mgmt/scripts/playbook.json ]; then
  echo "数据文件已过期，正在更新..."

  # 登录获取 token
  response=$(curl -s -X POST "http://127.0.0.1:8000/user/local-auth" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin"}')

  token=$(echo $response | jq -r '.data.token')

  # 更新三个文件
  curl -s -X POST "http://127.0.0.1:8000/playbook/published-versions" \
    -H "Cookie: aioe-auth=$token" \
    -o nanobot/skills/lion-mgmt/scripts/playbook.json

  curl -s -X POST "http://127.0.0.1:8000/tools/actions/available" \
    -H "Cookie: aioe-auth=$token" \
    -o nanobot/skills/lion-mgmt/scripts/actions.json

  curl -s -X GET "http://127.0.0.1:8000/docs/openapi.json" \
    -H "Cookie: aioe-auth=<token>" \
    -o nanobot/skills/lion-mgmt/scripts/openapi.json

  echo "数据文件更新完成"
else
  echo "数据文件仍然有效，跳过更新"
fi
```

### 错误处理

| 错误 | 处理方式 |
|------|----------|
| 文件不存在 | 视为需要更新，执行完整更新流程 |
| 登录失败 | 检查服务是否运行在 http://127.0.0.1:8000 |
| 接口调用失败 | 返回错误信息，不继续执行后续步骤 |
| stat 命令失败 | 视为文件不存在，执行更新流程 |

### 重要提醒

- ✅ 每次执行 skill 都必须先执行 Step 0
- ✅ 即使记忆中显示"文件刚更新过"，也要重新检查
- ✅ 文件检查失败时，视为需要更新
- ❌ 不要跳过文件检查步骤
- ❌ 不要假设文件是最新的

## 🔐 Step 1: 登录

```bash
curl -X POST "${LION_MGMT_BASE_URL}/user/local-auth" \
  -H "Content-Type: application/json" \
  -d '{"username":"${LION_MGMT_USERNAME}"}'
```

**响应**: 返回 token（格式：`account_id:jwt_token`），后续请求使用 Cookie: `aioe-auth=<token>`

## 📂 Step 2-4: 读取数据文件

**前置条件**: 文件已在 Step 0 中检查时效性并更新（如需要），现在可以安全读取最新数据。

**⚠️ 强制要求**: 每次都必须使用 `read_file` 工具重新读取，禁止使用记忆或缓存中的文件内容。

使用 `read_file` 工具依次读取：

1. `nanobot/skills/lion-mgmt/scripts/playbook.json` → 提取 `data.items`
2. `nanobot/skills/lion-mgmt/scripts/actions.json` → 提取 `data.items`
3. `nanobot/skills/lion-mgmt/scripts/openapi.json` → 提取 `paths`

**禁止行为**:
- ❌ 不要说"我记得上次读取的内容是..."
- ❌ 不要说"根据之前的文件内容..."
- ❌ 不要跳过读取步骤
- ✅ 必须每次都重新调用 `read_file` 工具

## 🎯 Step 5: 匹配逻辑

**⚠️ 强制要求**: 每次都必须重新匹配，禁止使用记忆中的历史匹配结果。

**禁止行为**:
- ❌ 不要说"我记得上次这个资源不可用"
- ❌ 不要说"根据之前的匹配结果，这个动作不存在"
- ❌ 不要因为记忆中的失败而跳过尝试
- ✅ 必须每次都重新从读取的文件中查找匹配
- ✅ 即使记忆中显示"资源不可用"，也要重新尝试

### 优先级 1: Playbook（剧本）

**匹配字段**: `name`, `description`, `tags`

**⚠️ 关键参数说明**:
1. **作战室 ID (`id`)**: 
   - 从当前会话上下文中获取（通常在 AI 消息的 metadata 中）
   - ❌ 不要查询作战室列表
   - ❌ 不要使用历史记忆中的旧 ID
   - ✅ 使用当前会话提供的作战室 ID

**调用接口**:
```http
POST ${LION_MGMT_BASE_URL}/warroom/run
Cookie: aioe-auth=<cookie>
Content-Type: application/json

{
  "id": "<当前会话的作战室ID>",
  "type": "flow",
  "playbook_input": {
    "flow_id": "<从 playbook 中提取>",
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

### 优先级 2: Action（动作）

**匹配字段**: `name`, `description`, `tags`

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
Cookie: aioe-auth=<cookie>
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

### 优先级 3: OpenAPI（底层接口）

**匹配字段**: `summary`, `description`, `operationId`

**调用接口**: 根据 OpenAPI 定义构造 HTTP 请求

### 优先级 4: AI 推理

如果前 3 个优先级都未匹配，使用 AI 能力：
- 分析用户需求
- 组合多个接口调用
- 提供解决方案建议

## 📤 Step 6: 处理响应并返回结果

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
   Cookie: aioe-auth=<cookie>
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
2. **禁止假设**: 不能假设 cookie 有效、文件未变
3. **严格顺序**: 必须先尝试 playbook，失败后才尝试 action，再失败才尝试 openapi
4. **路径必须完整**: 使用 `nanobot/skills/lion-mgmt/scripts/` 前缀
5. **作战室 ID 获取**（重要）:
   - ✅ 从当前会话上下文中获取作战室 ID
   - ❌ 不要查询作战室列表 (`/warroom/list`)
   - ❌ 不要使用历史记忆中的旧作战室 ID
6. **资源 ID 获取**（重要）:
   - ✅ 调用 `POST /tools/resource/detailed` 获取资源 ID
   - ✅ 使用响应中的 `data[0].id` 字段
   - ❌ 不要使用资源名称（如"微步在线-个人"）
7. **区分响应类型**:
   - Playbook/Action: 必须从响应中提取 `run_id`，并通过 run_id 查询执行结果，返回给用户
   - OpenAPI: 直接返回业务数据，无需提取 run_id
8. **禁止使用记忆中的资源状态**（重要）:
   - ❌ 禁止说"我记得上次这个资源不可用，所以跳过"
   - ❌ 禁止说"根据历史记录，这个动作不存在"
   - ❌ 禁止说"之前尝试过失败了，所以不再尝试"
   - ✅ 必须每次都重新读取文件、重新匹配、重新尝试
   - ✅ 资源、配置、数据随时可能更新，历史记忆不可靠

## 🔧 数据同步

**自动同步**: 文件会在每次执行 skill 时自动检查时效性（Step 0）。如果文件修改时间距离现在超过 24 小时，会自动调用接口更新。

**注意**: 不需要配置定时任务或手动同步脚本，skill 会自动管理文件的时效性。

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
1. ✅ 登录获取 cookie
2. ✅ 读取 playbook.json → 未找到匹配
3. ✅ 读取 actions.json → 找到 "IP分析" 动作
4. ✅ 调用 `/warroom/run` 执行动作
5. ✅ 获取响应: `run_id: "run_12345"`
6. ✅ 返回给用户:
   ```
   ✅ 已提交 IP 威胁情报查询任务
   执行 ID: run_12345
   结果: 1.1.1.1不危险
   ```

## 配置说明

本 skill 使用硬编码配置，无需设置环境变量：

- **服务地址**: `http://127.0.0.1:8000`（固定）
- **登录用户名**: `admin`（固定）
- **认证方式**: 通过 `/user/local-auth` 接口获取 token（仅限本地访问）
