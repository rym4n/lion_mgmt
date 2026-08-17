# 105 迁移故障注入测试

**目标**: 验证迁移在异常场景下的鲁棒性与回滚能力。

## 测试环境要求

- 独立测试数据库（非生产）
- 可重复执行（每次测试前恢复初始快照）
- 启用事务日志

## 故障场景 1: 同名冲突大量存在

### 模拟数据

```sql
-- 创建 10 个租户，每个租户都有名为 "通义千问" 的配置
DO $$
DECLARE
    i INT;
    tenant_uuid UUID;
BEGIN
    FOR i IN 1..10 LOOP
        tenant_uuid := gen_random_uuid();
        INSERT INTO llm_model_config (
            id, tenant_id, name, base_url, api_key, model_name,
            purpose, enabled, deleted, ctime, utime
        ) VALUES (
            gen_random_uuid(),
            tenant_uuid,
            '通义千问',
            'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'sk-test-' || i,
            'qwen-max',
            'chat',
            TRUE,
            FALSE,
            NOW(),
            NOW()
        );
    END LOOP;
END $$;
```

### 执行迁移

```bash
aerich upgrade
```

### 验证

```sql
-- 验证所有配置都已迁移，且冲突配置被改名
SELECT name, COUNT(*) as count
FROM llm_model_config
WHERE deleted = FALSE
  AND tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
GROUP BY name;
-- 预期: 1 行 name='通义千问', 9 行 name='通义千问-xxxxxxxx'

-- 验证总数不变
SELECT COUNT(*) FROM llm_model_config WHERE deleted = FALSE;
-- 预期: 等于迁移前的数量
```

### 预期结果

- ✅ 迁移成功，无数据丢失
- ✅ 9 个冲突配置被改名
- ✅ 改名格式为 `{原名}-{租户ID前8位}`

---

## 故障场景 2: 员工绑定指向已软删除的配置

### 模拟数据

```sql
-- 创建一个配置并绑定到员工
INSERT INTO llm_model_config (
    id, tenant_id, name, base_url, api_key, model_name,
    purpose, enabled, deleted, ctime, utime
) VALUES (
    'dead-config-id'::UUID,
    'tenant-abc'::UUID,
    '已删除配置',
    'https://x/v1',
    'sk-dead',
    'model-x',
    NULL,
    TRUE,
    TRUE,  -- 已软删除
    NOW(),
    NOW()
);

-- 创建员工并绑定到已删除的配置（模拟脏数据）
UPDATE command_agent
SET llm_config_id = 'dead-config-id'::UUID
WHERE agent_key = 'test-employee-1'
  AND deleted = FALSE;
```

### 执行迁移

```bash
aerich upgrade
```

### 验证

```sql
-- 验证软删除的配置也被迁移到 default 租户
SELECT tenant_id, deleted, name
FROM llm_model_config
WHERE id = 'dead-config-id'::UUID;
-- 预期: tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18', deleted = TRUE

-- 验证员工绑定仍然有效（外键未断）
SELECT ca.agent_key, ca.llm_config_id, lmc.name
FROM command_agent ca
LEFT JOIN llm_model_config lmc ON ca.llm_config_id = lmc.id
WHERE ca.agent_key = 'test-employee-1'
  AND ca.deleted = FALSE;
-- 预期: lmc.name = '已删除配置'（外键未断）
```

### 预期结果

- ✅ 软删除的配置也被迁移
- ✅ 员工绑定未断开
- ✅ resolve_llm 会因 deleted=TRUE 回落到默认配置（业务逻辑）

---

## 故障场景 3: default 租户已有配置（迁移目标冲突）

### 模拟数据

```sql
-- default 租户已有 5 个配置
INSERT INTO llm_model_config (
    id, tenant_id, name, base_url, api_key, model_name,
    purpose, enabled, deleted, ctime, utime
) VALUES
    (gen_random_uuid(), '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18', '宏云-主力', 'https://a/v1', 'sk-1', 'm1', 'chat', TRUE, FALSE, NOW(), NOW()),
    (gen_random_uuid(), '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18', '宏云-轻量', 'https://a/v1', 'sk-2', 'm2', 'lite', TRUE, FALSE, NOW(), NOW()),
    (gen_random_uuid(), '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18', '向量-v4', 'https://a/v1', 'sk-3', 'emb-v4', 'embedding', TRUE, FALSE, NOW(), NOW()),
    (gen_random_uuid(), '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18', '通义千问', 'https://b/v1', 'sk-4', 'qwen', NULL, TRUE, FALSE, NOW(), NOW()),
    (gen_random_uuid(), '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18', 'GPT-4', 'https://c/v1', 'sk-5', 'gpt-4', NULL, TRUE, FALSE, NOW(), NOW());

-- 其他租户也有名为 "通义千问" 和 "GPT-4" 的配置
INSERT INTO llm_model_config (
    id, tenant_id, name, base_url, api_key, model_name,
    purpose, enabled, deleted, ctime, utime
) VALUES
    (gen_random_uuid(), 'tenant-other-1'::UUID, '通义千问', 'https://x/v1', 'sk-x1', 'qwen-max', 'chat', TRUE, FALSE, NOW(), NOW()),
    (gen_random_uuid(), 'tenant-other-2'::UUID, 'GPT-4', 'https://x/v1', 'sk-x2', 'gpt-4-turbo', 'lite', TRUE, FALSE, NOW(), NOW());
```

### 执行迁移

```bash
aerich upgrade
```

### 验证

```sql
-- 验证 default 租户的配置数量增加
SELECT COUNT(*) FROM llm_model_config
WHERE tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
  AND deleted = FALSE;
-- 预期: 7 个（原 5 个 + 新迁入 2 个）

-- 验证冲突配置被改名
SELECT name FROM llm_model_config
WHERE tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
  AND deleted = FALSE
  AND name ~ '-(通义千问|GPT-4)';
-- 预期: 包含改名后的配置

-- 验证原 default 配置未受影响
SELECT name, model_name FROM llm_model_config
WHERE tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
  AND name IN ('宏云-主力', '宏云-轻量', '向量-v4')
  AND deleted = FALSE;
-- 预期: 3 行，model_name 不变
```

### 预期结果

- ✅ default 租户的原有配置不受影响
- ✅ 冲突的迁入配置被改名
- ✅ 总配置数 = 原 default 数 + 其他租户数

---

## 故障场景 4: 迁移中断（事务回滚）

### 模拟中断

```sql
-- 手动在迁移 SQL 中插入一个会失败的语句
-- 修改 105 迁移文件，在最后添加：
-- INSERT INTO non_existent_table VALUES (1);

-- 或者在迁移执行中途杀掉进程
```

### 执行迁移

```bash
aerich upgrade
# 预期: 迁移失败，事务回滚
```

### 验证

```sql
-- 验证 dimensions 列未添加
SELECT column_name FROM information_schema.columns
WHERE table_name = 'llm_model_config'
  AND column_name = 'dimensions';
-- 预期: 0 行

-- 验证租户未收敛（仍然是多租户）
SELECT DISTINCT tenant_id, COUNT(*) FROM llm_model_config
WHERE deleted = FALSE
GROUP BY tenant_id;
-- 预期: 多行（与迁移前一致）

-- 验证菜单行未插入
SELECT * FROM menu_config WHERE path = '/settings/llm-config';
-- 预期: 0 行
```

### 预期结果

- ✅ RUN_IN_TRANSACTION=True 生效
- ✅ 所有变更被回滚
- ✅ 数据库状态与迁移前完全一致
- ✅ aerich history 显示迁移未执行

---

## 测试执行清单

- [ ] 场景 1: 同名冲突大量存在
- [ ] 场景 2: 员工绑定指向已软删除的配置
- [ ] 场景 3: default 租户已有配置
- [ ] 场景 4: 迁移中断（事务回滚）

## 通过标准

所有场景的预期结果全部满足，且：
- 无数据丢失
- 无外键悬空
- 无孤立记录
- 可成功回滚

## 失败处理

如果任何场景失败:
1. 记录失败的 SQL 输出
2. 检查迁移日志与 PG 日志
3. 修正迁移脚本
4. 重新执行所有 4 个场景
