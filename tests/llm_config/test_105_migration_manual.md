# 105 迁移全链路测试手册

**目标**: 验证 105 迁移在真实 PG 环境的完整性与安全性。

## 前置条件

1. 准备测试数据库（非生产环境）
2. 加载存量数据快照（包含多租户配置）
3. 确保有至少 3 个租户的 llm_model_config 数据

## 测试步骤

### Step 1: 迁移前状态检查

```sql
-- 1.1 记录迁移前配置数量
SELECT tenant_id, COUNT(*) as count
FROM llm_model_config
WHERE deleted = FALSE
GROUP BY tenant_id;

-- 1.2 检查是否有同名冲突（会被改名）
SELECT name, COUNT(DISTINCT tenant_id) as tenant_count
FROM llm_model_config
WHERE deleted = FALSE
  AND tenant_id != '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
GROUP BY name
HAVING COUNT(DISTINCT tenant_id) > 1;

-- 1.3 记录员工绑定情况
SELECT tenant_id, llm_config_id, COUNT(*) as employee_count
FROM command_agent
WHERE llm_config_id IS NOT NULL
  AND deleted = FALSE
GROUP BY tenant_id, llm_config_id;
```

### Step 2: 执行迁移

```bash
cd /opt/dev/aioe/lion_mgmt
aerich upgrade
```

### Step 3: 迁移后验证

```sql
-- 3.1 验证所有配置已收敛到 default 租户
SELECT tenant_id, COUNT(*) as count
FROM llm_model_config
WHERE deleted = FALSE
GROUP BY tenant_id;
-- 预期: 只有一行，tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'

-- 3.2 验证 dimensions 列已添加
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'llm_model_config'
  AND column_name = 'dimensions';
-- 预期: data_type = 'integer', is_nullable = 'YES'

-- 3.3 验证菜单行已插入
SELECT id, name, path, tenant_id
FROM menu_config
WHERE path = '/settings/llm-config'
  AND deleted = FALSE;
-- 预期: 恰好一行

-- 3.4 验证员工绑定仍然有效（外键未断）
SELECT ca.id, ca.tenant_id, ca.llm_config_id, lmc.name
FROM command_agent ca
LEFT JOIN llm_model_config lmc ON ca.llm_config_id = lmc.id
WHERE ca.llm_config_id IS NOT NULL
  AND ca.deleted = FALSE;
-- 预期: 所有行的 lmc.name 不为 NULL（外键未悬空）

-- 3.5 验证冲突配置已改名
SELECT name
FROM llm_model_config
WHERE deleted = FALSE
  AND name ~ '-[0-9a-f]{8}$';
-- 预期: 如果迁移前有同名冲突，这里会列出改名后的配置
```

### Step 4: 应用层验证

```bash
# 4.1 启动服务
python server.py

# 4.2 调用 catalog 接口（任意租户可见）
curl -X POST http://localhost:8000/llm-config/catalog \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json"
# 预期: 返回所有 default 租户的配置

# 4.3 调用管理接口（仅 default 租户会话）
curl -X POST http://localhost:8000/llm-config/list \
  -H "Authorization: Bearer <default-tenant-token>" \
  -H "Content-Type: application/json"
# 预期: 返回配置列表

# 4.4 非 default 租户调用管理接口
curl -X POST http://localhost:8000/llm-config/list \
  -H "Authorization: Bearer <other-tenant-token>" \
  -H "Content-Type: application/json"
# 预期: 返回 {"code": 4005, ...} FORBIDDEN
```

### Step 5: 回滚测试（可选）

```bash
# 在测试环境执行回滚
aerich downgrade

# 验证 dimensions 列已删除
SELECT column_name FROM information_schema.columns
WHERE table_name = 'llm_model_config'
  AND column_name = 'dimensions';
-- 预期: 0 行

# 验证菜单行已删除
SELECT * FROM menu_config WHERE path = '/settings/llm-config';
-- 预期: 0 行（或已软删除）
```

## 预期结果

- ✅ 所有配置收敛到 default 租户
- ✅ dimensions 列存在且可空
- ✅ 菜单行已插入
- ✅ 员工绑定未断开
- ✅ 冲突配置已改名
- ✅ 非 default 租户无法访问管理接口
- ✅ 任意租户可访问 catalog 接口

## 失败处理

如果任何验证失败:
1. 记录具体的 SQL 查询结果
2. 检查迁移日志
3. 执行 `aerich downgrade` 回滚
4. 修正迁移脚本后重新测试

## 注意事项

- ⚠️ **生产环境迁移前必须备份数据库**
- ⚠️ 迁移窗口建议 15 分钟
- ⚠️ 如果配置数量 > 1000，评估迁移耗时
