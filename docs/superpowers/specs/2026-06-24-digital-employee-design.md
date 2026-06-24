# 数字员工（Digital Employee）模块设计

- 日期：2026-06-24
- 范围：lion_mgmt（本仓库）`services/digital_employee/` 模块新增设计；附 lion_agent / lion_memory 的对外契约与配置
- 作者：通过 brainstorming skill 与产品方多轮对齐落地
- 状态：待用户审阅

---

## 1. 背景与目标

在现有"指挥中心 + Nanobot 内置助手"基础上，扩展出数字员工产品形态：以 AIOE 为能力底座，通过 AI Agent 实现 7×24 在线、多岗位、可扩展的运维/安全数字员工。

8 个内置岗位：

1. AI 监控值班员（运维 + 安全 7×24 值班合一）
2. AI 漏洞管理员（漏洞扫描 + 跟踪 + 验证闭环）
3. AI 网络运维工程师（网络优化 + 巡检 + 排障 + 备份）
4. AI 数据库运维工程师（备份 + 慢查询调优 + 排障）
5. AI 云运维工程师（配置检查 + 资源规划）
6. AI 桌面运维工程师（补丁 + 合规检查 + 安全加固 + 配置管理）
7. AI 应用运维工程师（排障 + 审计）
8. AI 运营分析师（封装现有 `services/agent/report/*` 汇报助手能力）

要求：

- 不同岗位具备不同的专项技能与权限
- 既可扩展数字员工的技能，也可继续扩展更多岗位
- 岗位/技能配置与平台代码完全解耦，与"内置剧本/应用"一样通过包导入交付

## 2. 总体架构

### 2.1 三服务 + 一中间件

```
┌─────────────────────────────────────────────────────────────────────┐
│ lion_mgmt (Sanic) — 已有                                             │
│   管理面：员工/岗位/技能/包/报告 CRUD                                  │
│   DigitalEmployee* 表 + CommandTask                                  │
│   ExecuteCoordinator：调 lion_agent /execute/stream，路由 SSE 事件     │
│   EmployeeReport 异步抽取                                             │
└──────────────┬──────────────────────────────────────────────────────┘
               │ HTTP（短）/ SSE（长）
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ lion_agent (FastAPI + nanobot SDK) — 新建                            │
│   /opt/dev/aioe/lion_agent/                                          │
│   /internal/v1/execute(/stream) + /workspaces/<id>/{sync|delete}     │
│   workspace 持久化（含 sessions/）+ BotPool + ConcurrencyLimiter      │
└──────────────┬──────────────────────────────────────────────────────┘
               │ HTTP（add/cognify/search/users）
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ lion_memory (= Cognee 镜像 + 配置) — 新建                            │
│   /opt/dev/aioe/middleware/lion_memory/                              │
│   零代码改动；与 lion_kb / lion_redis 等同级中间件                     │
│   ENABLE_BACKEND_ACCESS_CONTROL=true，每 (User, Dataset) 物理隔离      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ lion_action — 已有                                                   │
│   不参与数字员工 execution                                            │
│   仅提供内置动作 digital_employee.dispatch_task（剧本/定时/巡检派单用） │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块边界

| 模块 | 职责 | 对本设计的影响 |
|---|---|---|
| `services/digital_employee/`（新） | 岗位/技能/员工 CRUD + 包导入 + 执行协调 + 报告生成 | 本次新建 |
| `services/command_center/` | 任务调度引擎（`CommandTask` + 事件流 + 调度决策） | `update_task_progress` 末尾追加一行异步 hook，其余不动 |
| `services/agent/` | 底层 `CommandAgent` CRUD + Nanobot 直接执行 + 报告引擎 | 完全保留；运营分析师岗位的 bindings 引用 agent_bp 的 report 接口 |
| `services/event` / `timed` / `inspection` / `workflow` / `tools` | 事件/定时/巡检/剧本/动作 | 一律不改代码；通过内置动作 `digital_employee.dispatch_task` 把工作派给数字员工 |
| `models/` | Tortoise ORM | 新增 `digital_employee.py`、`employee_report.py`；扩展 `command_center.py` 中 `CommandAgent` 字段 |

### 2.3 关键决策摘要

| 决策点 | 选择 | 理由 |
|---|---|---|
| 数据建模层级 | 三层：岗位 + 技能 + 员工 | 岗位/技能可独立扩展、独立授权、独立内置 |
| 技能形态 | 引用编排（bindings 指向剧本/动作/MCP/nanobot skill） | 复用现有底座，不重新发明执行单元 |
| 工作触发 | 事件 / 定时 / 人工 / 巡检 四来源统一汇入 `CommandTask` | 不引入"订阅表"等新概念，复用现有 event-to-playbook、timed、inspection 联动 |
| 权限 | 复用现有 RBAC：员工绑 Role，最终落到剧本/动作执行；高危走剧本审批节点 | 不发明新权限模型 |
| 内置 vs 自定义 | 都跨租户内置 + 租户自定义；内置走包导入，与"内置剧本/应用"一致 | 跨租户产品迭代体验 |
| 岗位定义形态 | yaml 结构化字段为 canonical；UI 引导用户填业务概念，后台渲染成 SOUL/AGENTS/TOOLS.md | 用户不需要懂 Nanobot 文件结构 |
| 任务报告 | 任务级 `EmployeeReport`（新表）+ 周期级 `ReportDocument`（运营分析师产出，复用现有） | 两个粒度对应不同产品场景 |
| 运行时 | 独立 lion_agent 服务持有 nanobot SDK + 持久 workspace | 解决记忆延续 + lion_mgmt 不被 LLM 拖慢 |
| 长期记忆 | lion_memory（Cognee 镜像）作为中间件 | 复用 Cognee 原生多租户与 cognify 能力，零代码 |

## 3. 数据模型

### 3.1 新增模型 `models/digital_employee.py`

#### `DigitalEmployeeRole`（岗位索引表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `role_key` | CharField(64) | 岗位逻辑标识（即模板目录名） |
| `name` | CharField(255) | 岗位名称 |
| `description` | TextField, null | 一句话简介 |
| `template_dir` | CharField(512) | role.yaml 所在目录绝对路径 |
| `default_skill_keys` | JSONField, default [] | 默认技能 key 列表 |
| `default_rbac_role_id` | CharField(36), null | 默认绑定的现有 `Role` |
| `license_required` | BoolField, default False | 是否需要 license 授权 |
| `version` | IntField, default 1 | 模板版本（升级触发条件） |
| `is_builtin` | BoolField, default False | 是否内置（跨租户可见） |
| `enabled` | BoolField, default True | 是否上架 |
| `meta` | JSONField, default {} | 扩展位 |

约束：`unique_together = (("tenant_id", "role_key"),)`；`indexes = (("tenant_id", "is_builtin"),)`；`table = "digital_employee_role"`。

#### `DigitalEmployeeSkill`（技能索引表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `skill_key` | CharField(64) | 技能逻辑标识（即技能目录名） |
| `name` | CharField(255) | 技能名称 |
| `description` | TextField, null | 技能说明 |
| `skill_dir` | CharField(512) | manifest.yaml 所在目录绝对路径 |
| `bindings` | JSONField, default {} | `{playbook_ids:[], action_keys:[], mcp_tools:[], nanobot_skills:[]}` |
| `require_approval` | BoolField, default False | 高危标识（仅文档作用，强管控仍走剧本审批节点） |
| `version` | IntField, default 1 | 技能版本 |
| `is_builtin` | BoolField, default False | 是否内置 |
| `enabled` | BoolField, default True | 是否上架 |
| `meta` | JSONField, default {} | 扩展位 |

约束：`unique_together = (("tenant_id", "skill_key"),)`；`indexes = (("tenant_id", "is_builtin"),)`；`table = "digital_employee_skill"`。

#### `EmployeeReport`（任务级报告表，置于 `models/employee_report.py`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | FK → `CommandTask`, related="employee_reports" | 关联任务 |
| `employee` | FK → `CommandAgent`, null | 产出员工 |
| `role_key` | CharField(64) | 产出岗位 |
| `title` | CharField(255) | 报告标题 |
| `severity` | CharField(20), default "info" | info/low/medium/high/critical |
| `observation` | TextField, null | 观察 |
| `analysis` | TextField, null | 分析 |
| `actions` | JSONField, default [] | `[{description, run_id, status, summary}, ...]` |
| `conclusion` | TextField, null | 结论 |
| `evidence_urls` | JSONField, default [] | 证据链（截图/日志 URL） |
| `report_document_id` | CharField(36), null | 关联 `ReportDocument`（仅运营分析师跨任务汇总时填） |
| `auto_generated` | BoolField, default True | 自动生成 vs 人工编辑过 |

约束：`indexes = (("tenant_id", "role_key"), ("tenant_id", "task_id"), ("tenant_id", "severity"))`；`table = "digital_employee_report"`。

#### `TenantCogneeCredential`（租户 ↔ Cognee 用户映射，置于 `models/digital_employee.py`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `tenant_id` | CharField(36), unique | AIOE 租户 ID |
| `cognee_user_id` | CharField(64) | Cognee 返回的 user UUID |
| `cognee_user_email` | CharField(255) | `{tenant_id}@aioe.internal` |
| `cognee_user_password_enc` | EncryptedCharField(255) | 注册时随机生成 |
| `cognee_jwt` | EncryptedCharField(2048), null | 缓存的 JWT |
| `cognee_jwt_expires_at` | DatetimeField, null | JWT 过期时间 |

约束：`table = "tenant_cognee_credential"`。

### 3.2 扩展现有 `CommandAgent`（新增字段，全部带默认值）

| 字段 | 类型 | 说明 |
|---|---|---|
| `role` | FK → `DigitalEmployeeRole`, null | 关联岗位；为 null 表示是数字员工产品之外的旧 Agent |
| `role_key` | CharField(64), null | 冗余 role.role_key，便于按岗位快速过滤 |
| `skill_keys` | JSONField, default [] | 该员工启用的技能列表 |
| `rbac_role_id` | CharField(36), null | 员工实际绑定的 RBAC 角色 |
| `resource_scope` | JSONField, default {} | 资源访问范围 |
| `workspace_synced_at` | DatetimeField, null | 上次同步到 lion_agent 的时间 |
| `workspace_etag` | CharField(64), null | role_version + skill_versions 派生 |

`agent_key` 仍作为唯一标识，员工创建时由 `{role_key}-{seq}` 派生（可被覆盖）；`capabilities` 由 `role.default_capabilities ∪ skill_capabilities` 派生写入；`system_instruction` / `prompt_config` 对数字员工不再使用，仅保留兼容旧 Agent。

### 3.3 `CommandTask` 零改动

复用现有 `source` / `assigned_agent` / `run_id` / `result` / `room` / `payload` 字段；`source` 取值扩展为 `manual` / `event` / `timed` / `inspection` / `heartbeat`。

### 3.4 内置数据的 tenant_id 约定

复用 `services/agent/views.py:285` 现有"is_builtin 跨租户可见"模式：内置 Role/Skill 用 sentinel tenant_id `"__builtin__"`，列表查询统一 `Q(tenant_id=current) | Q(is_builtin=True)`。

### 3.5 数据库迁移（一次 Aerich migrate）

1. 建 `digital_employee_role` / `digital_employee_skill` / `digital_employee_report` / `tenant_cognee_credential` 四张表
2. 给 `command_center_agent` 加 7 个新字段（默认值，向后兼容）
3. 数据迁移脚本写入 18 个新权限点到 `permissions_menu`
4. 内置岗位/技能数据**不**写入 —— 由部署后的包导入注入

## 4. 资源布局与岗位包

### 4.1 仓库内的岗位/技能模板源

```
agent-template/                  # 8 个内置岗位的包源
├── monitor/
│   ├── package.yaml             # type=role, role_key=monitor, version=1
│   ├── role.yaml                # 结构化定义
│   └── assets/                  # 可选
├── vuln_manager/
├── network_ops/
├── dba/
├── cloud_ops/
├── desktop_ops/
├── app_ops/
└── analyst/

skill-template/                  # 内置技能包源（跨岗位复用）
├── alert_triage/
├── vuln_scan/
├── slow_query_tuning/
├── period_report_compose/
└── ...
```

构建脚本：`tools/build_packages.py`，按目录打 zip 输出到 `dist/agents/` 与 `dist/skills/`，部署脚本调 `/digital_employee/package/import` 注入。

### 4.2 包格式

**岗位包**

```
<role_key>.role.zip
├── package.yaml          # type=role, role_key, version, vendor, license_required, is_builtin
├── role.yaml             # 结构化定义
└── assets/               # 可选
```

**技能包**

```
<skill_key>.skill.zip
├── package.yaml          # type=skill, skill_key, version, vendor
├── manifest.yaml         # 结构化定义 + bindings
└── assets/               # 可选
```

**Bundle 包（一次导入岗位 + 依赖技能 + 依赖剧本/动作 + RBAC 角色）**

```
<bundle_key>.bundle.zip
├── package.yaml          # type=bundle
├── roles/<role_key>/
├── skills/<skill_key>/
├── playbooks/            # 走 services/workflow 导入流
├── actions/              # 走 services/tools 导入流
└── rbac_roles/           # 写 Role 表
```

### 4.3 结构化字段（API 表单 + UI 引导项）

UI 不暴露 Nanobot 文件概念，引导用户填业务字段；后台渲染成 SOUL/AGENTS/TOOLS.md。

**岗位 `role.yaml` / `/digital_employee/role/save`**

| 字段 | UI 引导文案 | 渲染目标 |
|---|---|---|
| `key` | 岗位标识（英文） | 文件夹名 |
| `name` | 岗位名称 | DB |
| `description` | 一句话简介 | DB |
| `identity` | 岗位身份与定位 | SOUL.md |
| `principles` | 工作原则 | SOUL.md |
| `responsibilities` | 岗位职责清单 | AGENTS.md（开头） |
| `workflows[]` | 典型工作流程：场景 + 步骤 | AGENTS.md（中段） |
| `safety_rules` | 安全/合规底线 | AGENTS.md（结尾） |
| `tools_notes` | 工具使用注意事项 | TOOLS.md |
| `default_skills[]` | 默认技能 | DB |
| `default_rbac_role` | 默认绑定的 RBAC 角色 | DB |
| `license_required` | 是否需要 license | DB |
| `version` | 版本号 | DB |

**技能 `manifest.yaml` / `/digital_employee/skill/save`**

| 字段 | UI 引导文案 | 渲染目标 |
|---|---|---|
| `key` | 技能标识（英文） | 文件夹名 |
| `name` | 技能名称 | DB |
| `description` | 技能说明 | DB |
| `trigger_instruction` | 触发提示词 | instructions.md |
| `usage_notes` | 使用注意事项 | instructions.md |
| `bindings.playbook_ids[]` | 绑定的剧本 | manifest.yaml + DB |
| `bindings.action_keys[]` | 绑定的动作 | 同上 |
| `bindings.mcp_tools[]` | 绑定的 MCP 工具 | 同上 |
| `bindings.nanobot_skills[]` | 绑定的 Nanobot skill | 同上 |
| `require_approval` | 是否高危（仅文档） | manifest.yaml + DB |
| `version` | 版本号 | DB |

注：原计划的 `heartbeat_tasks[]` 字段移除 —— Nanobot SDK 集成下 HEARTBEAT.md 无触发载体；周期性工作完全靠 `timed` cron 实现。

### 4.4 内置 8 岗位字段骨架（产品方制作 bundle 时按此填写）

#### ① AI 监控值班员（`monitor`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 7×24 接收监控/安全告警，分诊（噪音抑制、严重性评估）、初步排障、必要时升级到对应专项岗位 |
| 默认技能 | `alert_triage`, `asset_health_inspect`, `incident_drill_down`, `shift_report_compose` |
| 默认 RBAC 角色 | `monitor_dispatcher` |
| 主要工作来源 | event + timed + manual |

#### ② AI 漏洞管理员（`vuln_manager`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 漏洞扫描 → 跟踪分配 → 修复验证 闭环 |
| 默认技能 | `vuln_scan`, `vuln_track`, `vuln_verify`, `vuln_weekly_summary` |
| 默认 RBAC 角色 | `vuln_operator` |
| 主要工作来源 | timed + event + manual |

#### ③ AI 网络运维工程师（`network_ops`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 网络巡检 + 流量优化 + 排障 + 设备配置备份 |
| 默认技能 | `network_inspect`, `network_traffic_optimize`, `network_troubleshoot`, `device_config_backup` |
| 默认 RBAC 角色 | `network_operator` |
| 主要工作来源 | event + timed + manual |

#### ④ AI 数据库运维工程师（`dba`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 备份核验 + 慢查询调优 + 排障 |
| 默认技能 | `db_backup_check`, `slow_query_tuning`, `db_troubleshoot` |
| 默认 RBAC 角色 | `dba_operator` |
| 主要工作来源 | event + timed + manual |

#### ⑤ AI 云运维工程师（`cloud_ops`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 云资源配置合规检查 + 资源规划 + 成本评审 |
| 默认技能 | `cloud_config_audit`, `cloud_capacity_plan`, `cloud_cost_review` |
| 默认 RBAC 角色 | `cloud_operator` |
| 主要工作来源 | timed + event + manual |

#### ⑥ AI 桌面运维工程师（`desktop_ops`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 终端补丁 + 合规检查 + 安全加固 + 配置管理 |
| 默认技能 | `patch_dispatch`, `endpoint_compliance_check`, `endpoint_hardening`, `endpoint_config_manage` |
| 默认 RBAC 角色 | `endpoint_operator` |
| 主要工作来源 | timed + event + manual |

#### ⑦ AI 应用运维工程师（`app_ops`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 应用排障 + 审计 |
| 默认技能 | `app_troubleshoot`, `app_audit_review`, `app_runtime_health` |
| 默认 RBAC 角色 | `app_operator` |
| 主要工作来源 | event + timed + manual |

#### ⑧ AI 运营分析师（`analyst`）

| 项 | 内容 |
|---|---|
| 岗位定位 | 周期报告（周/月/年）+ 工作成果汇报；包装现有 `services/agent/report/*` |
| 默认技能 | `period_report_compose`, `custom_data_fetch`, `report_polish`, `report_export_notify`（bindings 指向 `agent_bp` 的 report 接口） |
| 默认 RBAC 角色 | `analyst_operator` |
| 主要工作来源 | timed + manual |

### 4.5 RBAC 角色随包导入

每个 role 包内 `package.yaml` 声明依赖的 RBAC 角色定义（最小权限点列表）；导入时若 RBAC 角色不存在，自动在 `Role` 表创建为内置角色（`internal=true`）；已存在则跳过。

## 5. 模块结构与接口契约（lion_mgmt 端）

### 5.1 文件布局

```
services/digital_employee/
├── __init__.py
├── views.py                    # 蓝图 digital_employee_bp，所有 CRUD + 包导入 + 报告 API
├── manager.py                  # 业务编排（员工创建/技能切换/岗位升级）
├── prompt_builder.py           # render_workspace_files() 与 build_runtime_prompt()
├── execute_coordinator.py      # 持 SSE，调 lion_agent，路由事件
├── employee_report.py          # compose_report 异步生成
├── package_importer.py         # bundle/role/skill 三类包的拆分与导入
├── lion_agent_client.py        # httpx 封装 lion_agent 4 个接口
└── cognee_credential_manager.py # 租户 Cognee User 注册/登录/JWT 缓存

models/
├── digital_employee.py         # DigitalEmployeeRole / DigitalEmployeeSkill / TenantCogneeCredential
├── employee_report.py          # EmployeeReport
└── command_center.py           # 在原文件追加 CommandAgent 7 个新字段
```

### 5.2 关键函数签名

```python
# prompt_builder.py
def render_workspace_files(employee: CommandAgent) -> dict:
    """返回 {SOUL.md: ..., AGENTS.md: ..., TOOLS.md: ..., skills: {<key>: {instructions.md: ...}}}"""

def build_runtime_prompt(task: CommandTask, employee: CommandAgent) -> str: ...

# manager.py
async def create_employee(tenant_id, role_key, name, skill_keys, resource_scope, ...) -> CommandAgent: ...
async def update_employee_skills(employee: CommandAgent, skill_keys: list[str]) -> CommandAgent: ...
async def sync_employee_workspace(employee: CommandAgent) -> None: ...
async def upgrade_role_for_existing_employees(role: DigitalEmployeeRole) -> int: ...

# execute_coordinator.py
async def run(task: CommandTask, employee: CommandAgent) -> None:
    """对外不可见的协程；持 SSE，路由 delta/thinking/tool_call/progress/end 事件"""

# employee_report.py
async def compose(task: CommandTask, actions_draft: list[dict]) -> EmployeeReport: ...

# package_importer.py
async def import_package(file: bytes, is_builtin: bool, operator_id: str) -> ImportResult: ...
async def export_package(role_key: str | None = None, skill_key: str | None = None,
                         bundle: bool = False) -> bytes: ...

# cognee_credential_manager.py
async def get_or_refresh_jwt(tenant_id: str) -> str: ...
async def register_tenant_user(tenant_id: str) -> TenantCogneeCredential: ...
async def delete_tenant_user(tenant_id: str) -> None: ...
```

### 5.3 API 详单（蓝图 `digital_employee_bp`，前缀 `/digital_employee`）

#### 员工管理

| Method | Path | 说明 | 权限点 |
|---|---|---|---|
| POST | `/employee/create` | 基于岗位创建员工 + 同步 workspace | `digital_employee:c` |
| POST | `/employee/list` | 分页 + 过滤 | `digital_employee:r` |
| POST | `/employee/detail` | 详情 + 关联岗位 + 启用技能 + 最近 10 条任务 | `digital_employee:r` |
| POST | `/employee/update` | 改 name/description/max_concurrent/resource_scope | `digital_employee:u` |
| POST | `/employee/delete` | 软删 + offline + 调 lion_agent /workspaces/<id>/delete | `digital_employee:d` |
| POST | `/employee/skills/update` | 技能切换，触发增量 sync | `digital_employee:u` |
| POST | `/employee/scope/update` | 资源范围更新 | `digital_employee:u` |
| POST | `/employee/repair` | 物化失败时重试 sync | `digital_employee:u` |
| POST | `/employee/dispatch` | 派任务（`/command_center/tasks/create` 的薄包装） | `command_center:c` |
| POST | `/employee/tasks/list` | 列出员工历史任务，附 EmployeeReport 摘要 | `command_center:r` |

#### 岗位管理

| Method | Path | 说明 | 权限点 |
|---|---|---|---|
| POST | `/role/list` | 列岗位（OR 内置 + 当前租户） | `digital_employee:role:r` |
| POST | `/role/detail` | DB 字段 + role.yaml 全部结构化字段 | `digital_employee:role:r` |
| POST | `/role/save` | 创建/更新租户自定义岗位（按 role_key 幂等） | `digital_employee:role:c` 或 `:u` |
| POST | `/role/delete` | 软删；有员工引用则拒绝 | `digital_employee:role:d` |
| POST | `/role/upgrade-employees` | 该岗位所有员工重新 sync workspace | `digital_employee:role:u` |

#### 技能管理

| Method | Path | 说明 | 权限点 |
|---|---|---|---|
| POST | `/skill/list` | 列技能 | `digital_employee:skill:r` |
| POST | `/skill/detail` | DB + manifest.yaml 全部字段 + bindings 解析 | `digital_employee:skill:r` |
| POST | `/skill/save` | 创建/更新租户自定义技能 | `digital_employee:skill:c` 或 `:u` |
| POST | `/skill/delete` | 软删；有员工启用则拒绝 | `digital_employee:skill:d` |

#### 包管理

| Method | Path | 说明 | 权限点 |
|---|---|---|---|
| POST | `/package/import` | multipart 上传 zip；按 type 分发 role/skill/bundle | `digital_employee:package:import` |
| POST | `/package/export` | 按 key 导出；bundle 模式递归打包依赖 | `digital_employee:package:export` |
| POST | `/package/list` | 列出已导入包（DB 索引 + 物理校验） | `digital_employee:package:r` |
| POST | `/package/delete` | 卸载；有引用则拒绝 | `digital_employee:package:d` |

#### 报告管理

| Method | Path | 说明 | 权限点 |
|---|---|---|---|
| POST | `/report/list` | 分页 + 过滤（role_key / employee_id / severity / 时间） | `digital_employee:report:r` |
| POST | `/report/detail` | 详情 + 关联 task / runlogs / room 链接 + ReportDocument | `digital_employee:report:r` |
| POST | `/report/edit` | 人工修订；改后 `auto_generated=false` | `digital_employee:report:u` |
| POST | `/report/regenerate` | 重抽取 | `digital_employee:report:u` |

#### 内部回调（lion_agent → lion_mgmt）

| Method | Path | 说明 | 权限 |
|---|---|---|---|
| POST | `/runtime/transcript/save` | lion_agent 暂存 transcript（actions_draft 等）供 compose_report 用 | 内部 token |
| POST | `/runtime/transcript/fetch` | compose_report 拉 transcript | 内部 token |
| POST | `/runtime/report/save` | compose_report 抽取后回写（备用通道；通常是 lion_mgmt 内部直接落库） | 内部 token |
| POST | `/runtime/cognee/refresh-jwt` | JWT 过期兜底刷新 | 内部 token |

### 5.4 权限点全集（18 个，写入 `permissions_menu`）

```
digital_employee:c|r|u|d
digital_employee:role:c|r|u|d
digital_employee:skill:c|r|u|d
digital_employee:report:r|u
digital_employee:package:import|export|r|d
```

### 5.5 错误码（追加到 `core/error_code.py`）

```
DIGITAL_EMPLOYEE_ROLE_NOT_FOUND
DIGITAL_EMPLOYEE_SKILL_NOT_FOUND
DIGITAL_EMPLOYEE_EMPLOYEE_NOT_FOUND
WORKSPACE_SYNC_FAILED
PACKAGE_INVALID
PACKAGE_VERSION_LOWER
PACKAGE_TYPE_UNSUPPORTED
PACKAGE_PARTIAL_FAILURE
EMPLOYEE_HAS_PENDING_TASKS
ROLE_IN_USE_BY_EMPLOYEES
SKILL_IN_USE_BY_EMPLOYEES
COGNEE_AUTH_FAILED
LION_AGENT_UNAVAILABLE
```

### 5.6 settings 新增

```
LION_AGENT_ENDPOINT
LION_AGENT_INTERNAL_TOKEN
LION_MEMORY_ENDPOINT
LION_MEMORY_ADMIN_EMAIL
LION_MEMORY_ADMIN_PASSWORD
DIGITAL_EMPLOYEE_RESOURCES        # 包导入后的解压根目录
```

### 5.7 蓝图注册与 license

`services/__init__.py:bp_dict` 加入 `digital_employee_bp`，license 模块键 `digital_employee_bp`，与 `event_bp` / `workflow_bp` 等同级。

## 6. 工作来源接入

### 6.1 唯一接入点：内置动作 `digital_employee.dispatch_task`

随 8 个内置 bundle 一并交付的基础设施级动作（位于 bundle 的 `actions/` 子目录，由 `services/tools` 导入流注入）。

```yaml
action_key: digital_employee.dispatch_task
display_name: 派单给数字员工
inputs:
  title:                 { type: string,  required: true }
  description:           { type: string,  required: false }
  employee_agent_key:    { type: string,  required: false }
  required_capabilities: { type: array,   required: false }
  source:                { type: string,  required: false, default: "manual",
                           enum: [manual, event, timed, inspection, heartbeat] }
  priority:              { type: integer, required: false, default: 3 }
  payload:               { type: object,  required: false }
  room_id:               { type: string,  required: false }
  wait_completion:       { type: boolean, required: false, default: false }
outputs:
  task_id, run_id, agent_key, status, result
```

实现：动作内部调 `services/command_center/manager.create_command_task`；`wait_completion=true` 时轮询 task.status（间隔 1s，由 flow_timeout 控制超时）。

### 6.2 四类工作来源

| 来源 | 链路 |
|---|---|
| 事件 | `event` 模块匹配规则 → 触发剧本 → 剧本调 `digital_employee.dispatch_task` 动作 |
| 定时 | `timed` cron 到点 → 触发剧本/动作 → 调 `digital_employee.dispatch_task` |
| 巡检 | `InspectionSchedule.notify_config` / `custom_report_config` 配为 `dispatch_task` 动作 |
| 人工 | `/command_center/tasks/create`（带 `requested_agent_id`）或 `/digital_employee/employee/dispatch` |

四类来源经 `dispatch_task` 都进入 `CommandTask`，调度链路完全统一。

### 6.3 调度兼容

`services/command_center/dispatcher.py` 不动；员工的 `capabilities` 在创建/技能切换时由 `role.default_capabilities ∪ skill_capabilities` 自动派生，调度匹配逻辑保持不变。

### 6.4 不修改的模块清单

`services/event/` `event_ingestion/` `timed/` `inspection/` `workflow/` `tools/` `command_center/`（仅 `update_task_progress` 末尾追加一行 hook）`agent/` —— 一律不改代码。

## 7. 执行链路与 lion_agent 契约

### 7.1 端到端流程

```
[源] event/timed/inspection/manual
  │
  ▼
[playbook] 调 digital_employee.dispatch_task 动作
  │
  ▼
[lion_mgmt] /command_center/tasks/create
  ├ 创建 CommandTask（assigned_agent_key=employee-X）
  ├ 若 employee.workspace_synced_at 早于 role/skill 最新版本：
  │     prompt_builder.render_workspace_files(employee) → dict
  │     POST lion_agent /internal/v1/workspaces/<employee_id>/sync
  └ 异步任务：execute_coordinator.run(task, employee)
  │
  ▼
[lion_mgmt execute_coordinator]
  ├ cognee_jwt = await cognee_credential_manager.get_or_refresh_jwt(tenant_id)
  ├ POST lion_agent /internal/v1/execute/stream
  │   body = { workspace_id=employee_id, session_id=session_key,
  │            message=prompt, stream=true,
  │            cognee={ endpoint, jwt, dataset_name=employee_id } }
  ├ async for event in sse:
  │     delta     → chat_redis.publish(WR_TOPIC, ai-reply-delta)
  │     thinking  → chat_redis.publish(WR_TOPIC, ai-thinking)
  │     tool_call → actions_draft.append(...)
  │     progress  → command_center.update_task_progress(task, progress=...)
  │     end       → result = event.content
  ├ command_center.update_task_progress(task, status=completed, result)
  └ asyncio.create_task(employee_report.compose(task, actions_draft))
  │
  ▼
[lion_mgmt employee_report.compose] 异步
  ├ POST lion_agent /internal/v1/execute（非流式）
  │   message = LLM 抽取 prompt（含 transcript/actions_draft/runlogs 摘要）
  │   workspace_id = employee_id（复用员工 workspace）
  │   session_id = f"compose-report:{task.id}"
  │   cognee 同上
  ├ JSON 抽取 7 字段 → EmployeeReport.create(...)
  └ done
```

### 7.2 lion_agent 对外契约

`/opt/dev/aioe/lion_agent/`，FastAPI + nanobot SDK。

| Method | Path | 说明 |
|---|---|---|
| POST | `/internal/v1/execute` | 非流式执行；body 同 stream；返回 `{content, usage}` |
| POST | `/internal/v1/execute/stream` | SSE 执行；body `{workspace_id, session_id, message, request_id, cognee:{endpoint, jwt, dataset_name}}` |
| POST | `/internal/v1/workspaces/{id}/sync` | 同步 workspace；body `{files: {SOUL.md, AGENTS.md, TOOLS.md, skills:{<key>:{instructions.md}}}, role_version, skill_versions}`；不动 sessions/ |
| POST | `/internal/v1/workspaces/{id}/delete` | 软删 → mv 到 trash |
| GET | `/healthz` / `/readyz` | 健康检查 |

所有 `/internal/v1/*` 共享 `LION_AGENT_INTERNAL_TOKEN` 头校验。

### 7.3 lion_agent 项目骨架

```
/opt/dev/aioe/lion_agent/
├── pyproject.toml              # fastapi + nanobot-ai + httpx + loguru
├── app.py                      # /internal/v1/execute(/stream) + workspaces 接口
├── nanobot_runtime.py          # 移植 surewin-agent-api 同名文件
├── bot_pool.py                 # 移植
├── concurrency_limiter.py      # 移植
├── workspace_initializer.py    # 移植 + 加 sync_workspace_files()
├── workspace_sync_handler.py   # 处理 sync 接口的写盘 + BotPool 失效
├── memory/
│   ├── lion_memory_client.py   # httpx 封装 Cognee REST
│   └── memory_hook.py          # AgentHook：before_iteration 注入 + on_stream_end 写入
├── workspace-template/         # base 模板，仅 .nanobot/ 元数据骨架
├── config.json                 # nanobot config
├── runtime_support.py          # 移植
└── Dockerfile
```

### 7.4 SSE 不会拖累 lion_mgmt

| 顾虑 | 实际情况 |
|---|---|
| LLM 计算拖慢 lion_mgmt | 计算在 lion_agent；lion_mgmt 仅持 TCP 解析事件 |
| 高并发内存压力 | 单 SSE KB 级；asyncio 单进程千连接是常态 |
| Sanic worker 抢占 | 用 `request.respond()` + `asyncio.create_task` 主请求立即释放 |
| 失联恢复 | SSE 断开 → 任务 failed；lion_agent 端 nanobot session 仍保留，重派可复用 |

## 8. 长期记忆：lion_memory（= Cognee 镜像）

### 8.1 性质

lion_memory **就是 Cognee**，零代码改动；位于 `/opt/dev/aioe/middleware/lion_memory/`，与 `lion_kb` / `lion_redis` 等同级中间件。仓库内只放 Dockerfile / docker-compose.yaml / .env.example / entrypoint.sh / README，不含任何 Python 代码。

### 8.2 关键环境变量

| 类别 | 变量 | 取值 |
|---|---|---|
| 多租户 | `ENABLE_BACKEND_ACCESS_CONTROL` | `true` |
| 图谱后端 | `GRAPH_DATABASE_PROVIDER` | `kuzu`（内嵌；规模上来后切 `neo4j`） |
| 向量后端 | `VECTOR_DB_PROVIDER` | `pgvector`（复用 lion_db） |
| 向量库连接 | `VECTOR_DB_URL` / `VECTOR_DB_USER` / `VECTOR_DB_PASSWORD` | 指向 lion_db |
| 关系库 | `DB_PROVIDER` / `DB_HOST` / `DB_PORT` / `DB_USERNAME` / `DB_PASSWORD` | 指向 lion_db（独立 schema：`cognee`） |
| LLM | `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` | 指向 lion_ai |
| Embedding | `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | 指向 lion_ai 的 embedding |
| 认证 | `JWT_SECRET` / `ADMIN_USER_EMAIL` / `ADMIN_USER_PASSWORD` | 内部固定 |

### 8.3 多租户隔离模型（Cognee 原生）

```
Cognee 概念       AIOE 概念
User              一个 lion_mgmt tenant_id
Dataset           一个数字员工 (employee_id)
node_set          任务标签（task_id, role_key, asset_id, event_id 等）
```

`ENABLE_BACKEND_ACCESS_CONTROL=true` 时，每 (User, Dataset) 物理隔离的图库 + 向量库存储。

### 8.4 租户 Cognee User 生命周期（lion_mgmt 端）

```
租户开通时（在现有 tenant 创建逻辑尾部追加）：
  ├ password = secrets.token_urlsafe(32)
  ├ admin_token = await cognee_credential_manager._login_admin()  # 用 LION_MEMORY_ADMIN_EMAIL/PASSWORD 登录
  ├ POST cognee /api/v1/auth/register
  │   body { email: f"{tenant_id}@aioe.internal", password }
  │   header Authorization: Bearer <admin_token>
  ├ ← 200 { id: <cognee_user_id> }
  └ 落库 TenantCogneeCredential（password 加密）+ 立刻调 /api/v1/auth/jwt/login 缓存首次 JWT

数字员工创建/删除时：
  数字员工 dataset 由 Cognee 在 add() 时 lazy 创建；
  删除时调 /api/v1/datasets/<dataset_id>/delete（带租户 JWT）

租户注销时：
  用 admin_token 调 /api/v1/users/<id>/delete
```

### 8.5 JWT 流转（lion_mgmt → lion_agent → Cognee）

lion_agent 不存任何租户 token。每次任务派发时，lion_mgmt 在请求体里附带 `cognee.jwt`；lion_agent 创建 per-request `LionMemoryClient` 持有该 jwt，用完即销毁。

JWT 过期处理：lion_mgmt 派任务前主动刷新；对话过长导致中途过期时 lion_agent 拿 401 → 调内部回调 `/digital_employee/runtime/cognee/refresh-jwt` 拉新 jwt（兜底路径）。

### 8.6 lion_agent 调用 Cognee 的契约（直接 REST，无中间层）

| Method | Path | Body / Header | 用途 |
|---|---|---|---|
| POST | `/api/v1/add` | `Authorization: Bearer <租户 jwt>`；body `{data, dataset_name=employee_id, node_set, ...}` | 加文本到 dataset |
| POST | `/api/v1/cognify` | 同上；body `{datasets: [employee_id]}` | 触发图谱构建 |
| POST | `/api/v1/search` | 同上；body `{query_text, query_type="GRAPH_COMPLETION"\|"INSIGHTS", top_k, datasets, node_name}` | 检索记忆 |
| DELETE | `/api/v1/datasets/<dataset_id>` | 同上 | 删 employee dataset |
| POST | `/api/v1/auth/register` | 用 admin_token；lion_mgmt 调用 | 新租户注册 |
| POST | `/api/v1/auth/jwt/login` | email/password；lion_mgmt 调用 | 拿/刷新 jwt |
| POST | `/api/v1/users/<id>/delete` | 用 admin_token | 租户注销 |

### 8.7 lion_agent 的 memory_hook 行为

挂在 `NanobotClient.ask` 的 hooks：

- **`before_iteration`**：取本轮 user_message → search top-k 命中（按 role_key 等过滤）→ 通过 `context.inject_system_message` 注入 `<retrieved_memory>` 段；超时 2s 不阻塞主流程
- **`on_stream_end(resuming=false)`**：异步任务（不 await）调 `add` 加入 (user_msg, assistant_msg)，`node_set=[role_key, task_id, source_kind]`；接 `cognify` 排队
- **失败容错**：lion_memory 不可用 / 超时 → warn 日志，主对话不影响

### 8.8 性能与降级

| 场景 | 处理 |
|---|---|
| lion_memory 短时不可用 | hook try/except + 2s 超时；记忆功能降级 |
| cognify 排队过长 | fire-and-forget；Cognee 内部排队 |
| dataset 数量爆炸 | `ENABLE_BACKEND_ACCESS_CONTROL` 隔离已能承载千级；超大规模按 tenant 分片 lion_memory 实例 |
| LLM cognify 成本 | lion_memory 配 cheaper model 专用 cognify，与 lion_agent 主对话 LLM 解耦 |

## 9. 包导入流程

### 9.1 总流程

```
POST /digital_employee/package/import
  ↓
package_importer.import_package(file, is_builtin, operator_id):
  1. 解压到临时目录 /tmp/dep-import-<uuid>/
  2. 读 package.yaml，按 type 分流：
     - role   → import_role_package(...)
     - skill  → import_skill_package(...)
     - bundle → import_bundle_package(...)
  3. 分流处理后 commit 临时目录到目标位置
  4. 删除临时目录
```

### 9.2 `import_role_package`

- 校验 role.yaml schema
- 解析依赖技能：`role.default_skills[]` 必须已存在或在同包 bundle 中
- 写入位置：`is_builtin=true` → `<DIGITAL_EMPLOYEE_RESOURCES>/__builtin__/templates/<role_key>/`；否则 `<...>/<tenant_id>/templates/<role_key>/`
- 版本检查：DB 中已有 `(tenant_id, role_key)` 时，新 version > 旧 version 才覆盖；相等返回"已存在"；更低拒绝
- RBAC：若 `role.yaml.rbac_role_definition` 存在且未注册，写入 `Role` 表（`internal=true`）；存在跳过
- 写 / 更新 `DigitalEmployeeRole` 索引

### 9.3 `import_skill_package`

- 校验 manifest.yaml schema
- 解析 bindings：检查 `playbook_ids` / `action_keys` / `mcp_tools` 在租户可见范围内是否存在
  - is_builtin=true 跳过
  - 普通包缺失项 → warn 列入 `unresolved_bindings`，不阻塞
- 写盘 + 写 / 更新 `DigitalEmployeeSkill` 索引

### 9.4 `import_bundle_package`

包内 `roles/` `skills/` `playbooks/` `actions/` `rbac_roles/`。处理顺序（依赖向前）：

1. RBAC 角色 → `Role` 表
2. 动作 → `services/tools/import` 流程
3. 剧本 → `services/workflow/import` 流程
4. 技能 → `import_skill_package` 逐个
5. 岗位 → `import_role_package` 逐个

任一步失败：记下回滚清单，反序回滚已成功项；DB 用 `in_transaction()`；最终全成功才 commit。

### 9.5 升级与回滚

- 升级：上传 version 更高的同 key 包；lion_mgmt 异步对所有 enabled 引用员工调 `/employee/repair` 重新 sync workspace；运行中任务不受影响（lion_agent BotPool 命中旧版本）
- 卸载：`/digital_employee/package/delete`，前置校验"无引用"
- 强制回滚：手动导入旧版本包覆盖；员工自动 sync 到旧版本
- 数据：`EmployeeReport` 不回滚（追加），`CommandTask` 不回滚

## 10. 错误处理矩阵

| 失败场景 | 检测点 | 处理 |
|---|---|---|
| package.yaml 缺失 / type 非法 | `import_package` | `PACKAGE_INVALID`，HTTP 400 |
| version 不高于现有 | role/skill 包导入 | `PACKAGE_VERSION_LOWER`，HTTP 409 |
| bundle sub-package 失败 | bundle 处理 | 反向回滚 + `PACKAGE_PARTIAL_FAILURE` 含 step report |
| skill bindings 引用缺失（非内置） | `import_skill_package` | response.warnings；运行时再校验，缺失则任务失败 |
| workspace sync 失败 | `manager.create_employee` / `update_skills` | `WORKSPACE_SYNC_FAILED`；员工 status=error；提供 `/employee/repair` |
| lion_agent 不可用 | `execute_coordinator.run` | 任务 failed；CommandTaskEvent 记录；返还 load；不自动重试 |
| lion_memory 不可用 | `memory_hook` | 主对话不受影响；warn 日志 |
| nanobot session 卡死 | nanobot max_iterations + wall-clock 超时（默认 15min） | 主动断 SSE，任务 failed |
| compose_report LLM 抽取失败 | `employee_report.compose` | fallback：title=task.title、severity=info、observation=task.result 截断；warn 日志 |
| etag 不一致 | lion_agent sync 返回 mismatch | 重新 render + 重试一次 sync；仍失败人工介入 |
| 删员工时仍有未完成任务 | `delete_employee` | `EMPLOYEE_HAS_PENDING_TASKS` |
| 删岗位时仍有员工引用 | `delete_role` | `ROLE_IN_USE_BY_EMPLOYEES` |
| 删技能时仍被启用 | `delete_skill` | `SKILL_IN_USE_BY_EMPLOYEES` |
| Cognee 401 | lion_agent | 调 `/runtime/cognee/refresh-jwt` 拉新 jwt 重试一次；仍失败 → `COGNEE_AUTH_FAILED` |

## 11. 可观测

### 11.1 结构化日志（沿用 sanic logger.info 风格）

| 事件 | 关键字段 |
|---|---|
| `digital_employee.task_dispatched` | task_id, employee_agent_key, role_key, source, requested_agent_id |
| `digital_employee.workspace_sync` | employee_id, role_version, skill_versions_hash, duration_ms, status |
| `digital_employee.execution_started` | task_id, employee_id, lion_agent_session_id |
| `digital_employee.sse_event` | task_id, event_type, sequence_no |
| `digital_employee.execution_completed` | task_id, duration_ms, llm_tokens, tool_call_count, status |
| `digital_employee.report_composed` | task_id, report_id, severity, fallback_used |
| `digital_employee.package_imported` | package_type, key, version, is_builtin, operator_id, duration_ms |
| `digital_employee.error` | error_code, context |

### 11.2 Kafka 审计

`AUDIT_TOPIC` 追加：包导入/卸载、岗位/技能/员工增删改、报告人工修订、workspace sync。复用现有 `aproduce`。

### 11.3 指标（约定字段名，prometheus 接入由后续单独工作）

```
dr_active_employee_count{role_key}
dr_inflight_task_count{role_key, status}
dr_workspace_sync_duration_ms
dr_execution_duration_ms{role_key}
dr_report_compose_failures_total
dr_lion_agent_sse_disconnect_total
dr_lion_memory_call_duration_ms{op}
```

### 11.4 作战室可见性

`RedisStreamingHook` 协议（`libs/nanobot.py`）已就绪；execute_coordinator 把 SSE delta/thinking/tool_call 直接 publish 到 chat Redis 的 `WR_TOPIC`，前端作战室 WebSocket 自然呈现。无需新协议。

## 12. 部署 / 升级

### 12.1 首次部署

```
0. 中间件就绪：lion_db (PG with pgvector) / lion_redis / lion_kafka / lion_ai / lion_memory
1. 启动 lion_memory（拉 Cognee 镜像 + 注入 env，启动 uvicorn cognee.api.client:app）
   验证 ENABLE_BACKEND_ACCESS_CONTROL 实际生效（探测）
   等 /readyz OK
2. 启动 lion_agent
   配 LION_MEMORY_ENDPOINT、LLM provider；等 /readyz OK
3. 启动 lion_mgmt
   aerich upgrade（含 digital_employee 模块迁移）
   配 LION_AGENT_ENDPOINT、LION_MEMORY_ADMIN_EMAIL/PASSWORD
4. 启动 lion_action（现有部署不变）
5. 部署脚本调 /digital_employee/package/import 注入 8 个内置 bundle：
   aioe-digital-employees-builtin-monitor-v1.bundle.zip
   ...（共 8 个）
6. 可选：批量调 /digital_employee/employee/create 为该租户创建首批员工
```

### 12.2 岗位/技能版本升级

```
1. 部署脚本调 /package/import 上传新 version 包
2. lion_mgmt 写/更新 DB 索引；找出引用该岗位的所有 enabled 员工
3. 异步批量调 /employee/repair → 重新 render workspace → POST lion_agent /workspaces/<id>/sync
4. 升级期间正在执行的任务不受影响（BotPool 缓存现有版本）；新任务命中新版本
```

## 13. 测试策略

按现有项目惯例（无 tests 目录）：

1. **单元层（pytest）**：在 `tests/digital_employee/` 覆盖 `prompt_builder.render_workspace_files` / `package_importer.import_*` / `employee_report.compose` 三个纯逻辑模块；其余按 `python -m compileall services/digital_employee/` 通过即可
2. **集成层（手工）**：部署完整三服务 + 一中间件 + 一份测试 bundle，跑 happy path：派单 → workspace sync → 执行 → 报告生成 → 报告查看；记入 PR 描述
3. **回归保护**：现有 `services/agent/` 与 `services/command_center/` 接口保持兼容，不动旧接口；新接口都 `/digital_employee/` 前缀

## 14. 范围与不在范围

### 14.1 本次范围（lion_mgmt 端）

- `services/digital_employee/` 完整模块（views / manager / prompt_builder / execute_coordinator / employee_report / package_importer / lion_agent_client / cognee_credential_manager）
- `models/digital_employee.py` + `models/employee_report.py` + `command_center.py` 字段扩展
- 一次 aerich migration：4 张新表 + CommandAgent 7 个新字段 + 18 权限点 + 13 错误码
- `services/__init__.py` 注册 `digital_employee_bp`，license 模块键 `digital_employee_bp`
- `settings/__init__.py` 新增 6 个变量
- 一个示例 bundle 包（监控值班员）作为参考实现

### 14.2 本次不范围

- `lion_agent` 项目实现（独立项目独立 PR）
- `lion_memory` 镜像构建配置（独立项目独立 PR）
- 8 个内置 bundle 包的最终业务内容（产品方制作；本设计仅提供字段骨架）
- `services/agent/` `services/command_center/` `services/event/` `services/timed/` `services/inspection/` `services/workflow/` `services/tools/` —— 一律不动
- 数字员工大屏 / 看板 UI
- 跨员工 / 跨任务的高级运营报告

## 15. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| lion_agent 单点故障 | 数字员工任务全停 | 单实例先上线；规模上来后按 employee_id 哈希分片 + 健康检查切换 |
| Cognee cognify 成本失控 | LLM 账单上涨 | lion_memory 配 cheaper model 专用 cognify；同员工 1 分钟内多轮对话合并写入 |
| workspace sync 幂等性失败 | 员工 prompt 与产品方期望不一致 | etag 校验 + sync 失败时 status=error + repair；版本号严格递增 |
| 8 岗位的内置 bundle 内容质量 | 数字员工跑出来效果差 | 产品方迭代 role.yaml；每次只需重打 bundle 重新 import |
| 高危操作绕过 | 员工通过 nanobot exec/SSH 工具直接动手 | nanobot config.json 限制可用工具集合 + 高危必须走剧本审批节点；TOOLS.md 明确约束 |
| 多租户记忆串扰 | 一个租户的记忆被另一个租户检索到 | 完全依赖 Cognee 原生 `ENABLE_BACKEND_ACCESS_CONTROL`；启动期探测该开关实际生效；若 Cognee 隔离回退则数据可能串扰 |
| Cognee 版本兼容 | Cognee API 不向后兼容 | lion_memory 镜像锁定具体 tag；升级前在测试环境跑全链路验收 |
| JWT 中途过期 | 任务执行中调 cognee 401 | lion_agent 调 lion_mgmt `/runtime/cognee/refresh-jwt` 拉新 jwt 重试一次；兜底路径 |

## 16. 后续工作（不在本设计实现范围）

- lion_agent 项目落地（FastAPI + nanobot SDK + memory_hook + workspace sync handler）
- lion_memory 镜像与 docker-compose
- 8 个内置 bundle 包的内容编写
- 内置动作 `digital_employee.dispatch_task` 的具体实现
- 数字员工大屏 / 看板
- 与 prometheus 的指标接入


