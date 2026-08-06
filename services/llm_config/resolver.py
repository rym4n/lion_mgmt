# coding=utf-8
"""LLM 凭证解析：所有 LLM 调用点取凭证的唯一入口。

设计要点（见 SPEC §5.1）：
- 只读数据库，无任何环境变量分支。运行时唯一真相在 llm_model_config 表。
- 员工绑定的配置若已软删/被禁用/是脏数据，回落全局默认而不抛错。
"""
from dataclasses import dataclass, field

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError


@dataclass
class ResolvedLlm:
    base_url: str
    model_name: str
    config_id: str
    # repr=False：防止 api_key 随 dataclass 默认 repr 进入日志或异常堆栈
    api_key: str = field(repr=False, default="")

    def to_payload(self) -> dict:
        """序列化为 lion_agent ExecuteRequest.llm 的结构。"""
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model_name,
        }


async def _fetch(tenant_id: str, **conditions):
    from models import LlmModelConfig

    return await LlmModelConfig.filter(
        tenant_id=tenant_id, deleted=False, enabled=True, **conditions
    ).first()


async def resolve_llm(
    tenant_id: str,
    *,
    purpose: str = "chat",
    config_id: str | None = None,
) -> ResolvedLlm:
    """解析出可用的 LLM 凭证。

    优先级：
      1. config_id 显式指定（员工绑定）→ 按 id 取
      2. 上一步取不到（已软删/被禁用/脏数据）→ 回落第 3 步，不抛错
      3. 按 purpose 取
      4. 仍取不到 → 抛 LLM_CONFIG_NOT_CONFIGURED
    """
    record = None
    if config_id:
        record = await _fetch(tenant_id, id=config_id)
    if record is None:
        record = await _fetch(tenant_id, purpose=purpose)
    if record is None:
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)
    return ResolvedLlm(
        base_url=record.base_url,
        api_key=record.api_key,
        model_name=record.model_name,
        config_id=record.id,
    )
