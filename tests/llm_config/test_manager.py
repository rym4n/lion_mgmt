# coding=utf-8
import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# 确保项目根目录在 sys.path 中
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _FakeRow(SimpleNamespace):
    """假的 Tortoise 行对象。manager 里对单行的改动走 `await row.save()`（Tortoise 的实例保存语义），
    所以假行必须提供异步 save()。纯 SimpleNamespace 会报
    AttributeError: has no attribute 'save'。这里 save() 是空实现即可——
    行对象本身就在内存列表里，属性赋值已经生效。
    """

    async def save(self, *args, **kwargs):
        return None


class _FakeQuerySet:
    def __init__(self, items):
        self._items = items

    async def first(self):
        return self._items[0] if self._items else None

    async def all(self):
        return list(self._items)

    def order_by(self, *_args):
        return self

    async def update(self, **changes):
        for item in self._items:
            for key, value in changes.items():
                setattr(item, key, value)
        return len(self._items)

    async def count(self):
        return len(self._items)

    def __aiter__(self):
        async def gen():
            for item in self._items:
                yield item
        return gen()


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, **conditions):
        matched = []
        for row in self.rows:
            ok = True
            for key, value in conditions.items():
                if key.endswith("__icontains"):
                    field = key[: -len("__icontains")]
                    if value.lower() not in str(getattr(row, field, "")).lower():
                        ok = False
                        break
                elif getattr(row, key, None) != value:
                    ok = False
                    break
            if ok:
                matched.append(row)
        return _FakeQuerySet(matched)

    async def get_or_none(self, **conditions):
        return await self.filter(**conditions).first()

    async def create(self, **fields):
        row = _FakeRow(**{"deleted": False, "enabled": True, "purpose": None, **fields})
        if not hasattr(row, "id"):
            row.id = f"generated-{len(self.rows)}"
        self.rows.append(row)
        return row


class _FakeDb:
    def __init__(self):
        self.configs: list = []
        self.agents: list = []

    def add(self, **kwargs):
        base = {
            "id": "cfg-x", "name": "配置", "base_url": "https://x/v1",
            "api_key": "sk-key", "model_name": "aioe", "purpose": None,
            "enabled": True, "deleted": False, "tenant_id": "t1",
            "ctime": "2026-08-05 00:00:00", "utime": "2026-08-05 00:00:00",
        }
        base.update(kwargs)
        row = _FakeRow(**base)
        self.configs.append(row)
        return row

    def add_agent(self, **kwargs):
        base = {"id": "emp-x", "llm_config_id": None, "deleted": False, "tenant_id": "t1"}
        base.update(kwargs)
        row = _FakeRow(**base)
        self.agents.append(row)
        return row

    def load_manager(self):
        import importlib

        for name in (
            "models",
            "core",
            "core.error_code",
            "libs",
            "libs.error_code",
            "libs.error_code.exception",
            "services",
            "services.llm_config",
            "services.llm_config.manager",
        ):
            sys.modules.pop(name, None)

        config_table = _FakeTable(self.configs)
        agent_table = _FakeTable(self.agents)

        def _mask(value):
            if not value:
                return ""
            return "***" if len(value) <= 7 else f"{value[:3]}***{value[-4:]}"

        models_module = types.ModuleType("models")
        models_module.LlmModelConfig = config_table
        models_module.CommandAgent = agent_table
        models_module.mask_api_key = _mask
        sys.modules["models"] = models_module

        import services.llm_config.manager as manager_module
        return importlib.reload(manager_module)


@pytest.fixture
def fake_db():
    return _FakeDb()


def test_set_purpose_clears_previous_holder(fake_db):
    """同租户下 purpose 非空值唯一：设新的 chat 必须先清旧的。"""
    manager = fake_db.load_manager()
    old = fake_db.add(id="cfg-old", name="旧默认", purpose="chat")
    new = fake_db.add(id="cfg-new", name="新默认", purpose=None)

    asyncio.run(manager.set_purpose("t1", "cfg-new", "chat"))

    assert old.purpose is None, "旧的 chat 应被清空"
    assert new.purpose == "chat"


def test_set_purpose_does_not_touch_other_tenants(fake_db):
    manager = fake_db.load_manager()
    other = fake_db.add(id="cfg-other", name="别家默认", purpose="chat", tenant_id="t2")
    fake_db.add(id="cfg-mine", name="我家", purpose=None)

    asyncio.run(manager.set_purpose("t1", "cfg-mine", "chat"))

    assert other.purpose == "chat", "不得跨租户清除"


def test_lite_and_chat_coexist(fake_db):
    manager = fake_db.load_manager()
    chat = fake_db.add(id="cfg-chat", name="主力", purpose="chat")
    lite = fake_db.add(id="cfg-lite", name="轻量", purpose=None)

    asyncio.run(manager.set_purpose("t1", "cfg-lite", "lite"))

    assert chat.purpose == "chat", "设置 lite 不应影响 chat"
    assert lite.purpose == "lite"


def test_update_with_blank_api_key_keeps_existing(fake_db):
    """编辑表单不回填明文，留空表示不修改。"""
    manager = fake_db.load_manager()
    record = fake_db.add(id="cfg-1", name="配置", api_key="sk-original")

    asyncio.run(manager.update_config("t1", "cfg-1", name="改名", api_key=""))

    assert record.api_key == "sk-original"
    assert record.name == "改名"


def test_update_with_new_api_key_replaces(fake_db):
    manager = fake_db.load_manager()
    record = fake_db.add(id="cfg-1", name="配置", api_key="sk-original")

    asyncio.run(manager.update_config("t1", "cfg-1", api_key="sk-rotated"))

    assert record.api_key == "sk-rotated"


def test_list_masks_api_key(fake_db):
    manager = fake_db.load_manager()
    fake_db.add(id="cfg-1", name="配置", api_key="sk-abcdefghijklmn")

    rows = asyncio.run(manager.list_configs("t1"))

    assert rows[0]["api_key"] == "sk-***klmn"
    assert "sk-abcdefghijklmn" not in str(rows)
    assert "remark" not in rows[0]


def test_delete_clears_employee_bindings_and_reports_count(fake_db):
    """软删除不触发 on_delete=SET_NULL，必须显式清空（SPEC §4.3）。"""
    manager = fake_db.load_manager()
    record = fake_db.add(id="cfg-1", name="配置")
    a1 = fake_db.add_agent(id="emp-1", llm_config_id="cfg-1")
    a2 = fake_db.add_agent(id="emp-2", llm_config_id="cfg-1")
    a3 = fake_db.add_agent(id="emp-3", llm_config_id=None)

    affected = asyncio.run(manager.delete_config("t1", "cfg-1"))

    assert affected == 2
    assert a1.llm_config_id is None and a2.llm_config_id is None
    assert a3.llm_config_id is None
    assert record.deleted is True, "应为软删除而非物理删除"


def test_create_rejects_duplicate_name_including_soft_deleted(fake_db):
    """unique_together 不区分 deleted，同名重建必须报可理解的错（SPEC §7.1）。"""
    manager = fake_db.load_manager()
    fake_db.add(id="cfg-gone", name="宏云-aioe", deleted=True)
    from libs.error_code.exception import CustomError

    with pytest.raises(CustomError) as exc:
        asyncio.run(manager.create_config(
            "t1", name="宏云-aioe", base_url="https://x/v1",
            api_key="sk-1", model_name="aioe",
        ))
    assert exc.value.enum.code == 4181


@pytest.mark.parametrize(
    ("context_size", "trigger_ratio"),
    [
        (0, 0.8),
        (-1, 0.8),
        (True, 0.8),
        (64000.5, 0.8),
        (64000, 0),
        (64000, -0.1),
        (64000, 0.9001),
        (64000, True),
    ],
)
def test_create_rejects_invalid_context_settings(
    fake_db, context_size, trigger_ratio,
):
    manager = fake_db.load_manager()
    from libs.error_code.exception import CustomError

    with pytest.raises(CustomError):
        asyncio.run(manager.create_config(
            "t1", name="invalid", base_url="https://x/v1",
            api_key="sk-1", model_name="aioe",
            context_size=context_size, trigger_ratio=trigger_ratio,
        ))


def test_update_preserves_context_settings_when_optional_fields_are_none(fake_db):
    manager = fake_db.load_manager()
    record = fake_db.add(
        id="cfg-1",
        name="配置",
        context_size=64000,
        trigger_ratio=0.85,
    )

    asyncio.run(manager.update_config(
        "t1",
        "cfg-1",
        name="改名",
        context_size=None,
        trigger_ratio=None,
    ))

    assert record.name == "改名"
    assert record.context_size == 64000
    assert record.trigger_ratio == 0.85


@pytest.mark.parametrize(
    ("changes", "expected_context_size", "expected_trigger_ratio"),
    [
        ({"context_size": 96000}, 96000, 0.85),
        ({"trigger_ratio": 0.75}, 64000, 0.75),
    ],
)
def test_update_changes_one_context_setting_without_resetting_the_other(
    fake_db, changes, expected_context_size, expected_trigger_ratio,
):
    manager = fake_db.load_manager()
    record = fake_db.add(
        id="cfg-1",
        name="配置",
        context_size=64000,
        trigger_ratio=0.85,
    )

    asyncio.run(manager.update_config("t1", "cfg-1", **changes))

    assert record.context_size == expected_context_size
    assert record.trigger_ratio == expected_trigger_ratio
