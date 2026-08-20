# LLM 模型配置管理运维手册

**版本**: P6 (ReMe 迁移后)  
**更新日期**: 2026-08-17  
**适用范围**: lion_mgmt 2.1.0+

## 概述

P6 将 LLM 模型配置从"每租户一套"收敛为"平台级唯一配置池"。

## 核心变更

### 变更后（P6）
- 所有配置收敛到 default 租户 (8bce552d-c27c-41d4-8a1b-8c5bf38dfe18)
- 管理接口仅 default 租户可访问
- 普通租户通过 /llm-config/catalog 只读查看
- 员工只能绑定 default 租户的配置

## 迁移执行

### 1. 备份
```bash
pg_dump -t llm_model_config -t command_agent -t menu_config > backup_p6.sql
```

### 2. 执行迁移
```bash
systemctl stop lion_mgmt
aerich upgrade
systemctl start lion_mgmt
```

### 3. 验证
```sql
-- 所有配置已收敛
SELECT tenant_id, COUNT(*) FROM llm_model_config WHERE deleted = FALSE GROUP BY tenant_id;
-- 预期: 只有 default 租户

-- dimensions 列已添加
\d llm_model_config

-- 菜单行已插入
SELECT * FROM menu_config WHERE path = '/settings/llm-config';
```

## 回滚
```bash
systemctl stop lion_mgmt
aerich downgrade
psql < backup_p6.sql
systemctl start lion_mgmt
```

## 日常管理

### 添加配置
```bash
curl -X POST http://localhost:8000/llm-config/create \
  -H "Authorization: Bearer <default-tenant-token>" \
  -d '{
    "name": "向量-v5",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-xxx",
    "model_name": "text-embedding-v5",
    "purpose": "embedding",
    "dimensions": 1536
  }'
```

### 查看配置
```sql
SELECT id, name, model_name, purpose, enabled
FROM llm_model_config
WHERE tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
  AND deleted = FALSE;
```

## 故障排查

### 普通租户无法访问管理接口
**症状**: 返回 4005 FORBIDDEN  
**解决**: 使用 /llm-config/catalog 只读查看

### 员工绑定失败
**症状**: LLM_CONFIG_NOT_CONFIGURED  
**排查**: 检查配置是否属于 default 租户且未软删除

### resolve_llm 查不到配置
**排查**:
```sql
SELECT * FROM llm_model_config
WHERE tenant_id = '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'
  AND purpose = 'chat'  -- 或 'lite' / 'embedding'
  AND deleted = FALSE
  AND enabled = TRUE;
```

## 安全注意事项

1. 只有平台管理员应拥有 default 租户账号
2. API Key 加密存储 (AES-256-GCM)
3. 管理接口返回脱敏 key (前3后4)
4. catalog 接口不返回 API Key

---
**变更历史**: 2026-08-17 初版 (P6 迁移)
