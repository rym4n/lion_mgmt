# coding=utf-8
"""OpenViking embedding 配置校验单测。

配置格式以 middleware/lion_viking 的实际实现为准（JSON），
其 entrypoint.sh 用 ``python3 -m json.tool`` 校验、``sha256sum`` 比对哈希、
``"dimension": <int>`` 提取维度。
"""

import asyncio
import hashlib
import json

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.digital_employee import openviking
from services.digital_employee.openviking import (
    FIXED_DIMENSION,
    compute_content_hash,
    parse_config,
    parse_dimension,
    validate_embedding_config,
)

# 与 middleware/lion_viking/ov.conf 同构的真实配置
# v2: 维度固定为 2048
REAL_CONFIG = json.dumps(
    {
        "server": {"host": "0.0.0.0", "port": 8000, "auth_mode": "api_key"},
        "vlm": {"provider": "openai", "model": "gpt-4o"},
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "dimension": 2048,
            }
        },
        "storage": {"workspace": "/app/data"},
    },
    indent=2,
)


class TestParseDimension:
    def test_parses_nested_dense_dimension(self):
        assert parse_dimension(REAL_CONFIG) == 2048

    def test_parses_top_level_embedding_dimension(self):
        content = json.dumps({"embedding": {"dimension": 3072}})
        assert parse_dimension(content) == 3072

    def test_parses_numeric_string_dimension(self):
        content = json.dumps({"embedding": {"dense": {"dimension": "1024"}}})
        assert parse_dimension(content) == 1024

    def test_returns_none_when_dimension_absent(self):
        content = json.dumps({"embedding": {"dense": {"model": "text-embedding-3"}}})
        assert parse_dimension(content) is None

    def test_returns_none_for_empty_content(self):
        assert parse_dimension("") is None
        assert parse_dimension("   \n  ") is None

    def test_falls_back_to_flat_key_form(self):
        # 兼容 `embedding.dimension = 1536` 扁平写法
        assert parse_dimension("embedding.dimension = 1536") == 1536
        assert parse_dimension("[embedding]\ndimension = 2048\n") == 2048

    def test_ignores_boolean_dimension(self):
        content = json.dumps({"embedding": {"dimension": True}})
        assert parse_dimension(content) is None

    def test_matches_entrypoint_first_dimension_semantics(self):
        """entrypoint.sh 取首个 dimension，解析结果需一致。"""
        content = json.dumps({"embedding": {"dense": {"dimension": 2048}}})
        assert parse_dimension(content) == 2048


class TestParseConfig:
    def test_parses_valid_json(self):
        assert parse_config(REAL_CONFIG)["embedding"]["dense"]["dimension"] == 2048

    def test_rejects_malformed_json(self):
        # entrypoint.sh 会拒绝非法 JSON 并拒绝启动，此处必须提前拦截
        with pytest.raises(CustomError) as exc:
            parse_config("{ not json ")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON

    def test_rejects_non_object_json(self):
        with pytest.raises(CustomError) as exc:
            parse_config("[1, 2, 3]")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON


class TestComputeContentHash:
    def test_matches_sha256_of_utf8_bytes(self):
        expected = hashlib.sha256(REAL_CONFIG.encode("utf-8")).hexdigest()
        assert compute_content_hash(REAL_CONFIG) == expected
        assert len(compute_content_hash(REAL_CONFIG)) == 64

    def test_hash_changes_with_content(self):
        other = REAL_CONFIG.replace("2048", "1536")
        assert compute_content_hash(REAL_CONFIG) != compute_content_hash(other)

    def test_hash_is_stable(self):
        assert compute_content_hash(REAL_CONFIG) == compute_content_hash(REAL_CONFIG)


class TestValidateEmbeddingConfig:
    def test_returns_dimension_and_hash(self):
        dimension, content_hash = validate_embedding_config(REAL_CONFIG)
        assert dimension == 2048
        assert content_hash == hashlib.sha256(REAL_CONFIG.encode("utf-8")).hexdigest()

    def test_rejects_empty_content(self):
        with pytest.raises(CustomError) as exc:
            validate_embedding_config("")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON

    def test_rejects_whitespace_only_content(self):
        with pytest.raises(CustomError):
            validate_embedding_config("   \n\t ")

    def test_rejects_invalid_json(self):
        with pytest.raises(CustomError) as exc:
            validate_embedding_config("embedding.dimension = 1536")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON

    def test_rejects_missing_dimension(self):
        content = json.dumps({"embedding": {"dense": {"model": "text-embedding-3"}}})
        with pytest.raises(CustomError) as exc:
            validate_embedding_config(content)
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_MISSING_DIMENSION

    def test_rejects_dimension_not_2048(self):
        """维度必须为 2048，其他值一律拒绝（v2 架构约束）"""
        for dimension in [1536, 768, 3072, 4096, 1, 0, -1, 999999]:
            content = json.dumps({"embedding": {"dense": {"dimension": dimension}}})
            with pytest.raises(CustomError) as exc:
                validate_embedding_config(content)
            assert exc.value.enum == ErrorCode.EMBEDDING_DIMENSION_MISMATCH

    def test_accepts_dimension_2048(self):
        """只有 2048 维度通过校验"""
        content = json.dumps({"embedding": {"dense": {"dimension": 2048}}})
        dimension, content_hash = validate_embedding_config(content)
        assert dimension == 2048
        assert len(content_hash) == 64

    def test_fixed_dimension_constant_is_2048(self):
        """确保 FIXED_DIMENSION 常量为 2048"""
        assert FIXED_DIMENSION == 2048

    @pytest.mark.parametrize("dimension", [0, -1, 4097, 999999])
    def test_rejects_out_of_range_dimension_legacy(self, dimension):
        """保留旧测试用于回归检查（现在会触发 MISMATCH 而非 INVALID_DIMENSION）"""
        content = json.dumps({"embedding": {"dense": {"dimension": dimension}}})
        with pytest.raises(CustomError) as exc:
            validate_embedding_config(content)
        # v2 架构下，任何非 2048 的维度都是 MISMATCH
        assert exc.value.enum == ErrorCode.EMBEDDING_DIMENSION_MISMATCH

    @pytest.mark.parametrize("dimension", [1, 768, 1536, 3072, 4096])
    def test_rejects_non_2048_dimensions_v2(self, dimension):
        """v2: 即使是有效范围内的维度，只要不是 2048 都拒绝"""
        content = json.dumps({"embedding": {"dense": {"dimension": dimension}}})
        with pytest.raises(CustomError) as exc:
            validate_embedding_config(content)
        assert exc.value.enum == ErrorCode.EMBEDDING_DIMENSION_MISMATCH


# ---------------------------------------------------------------------------
# Task 3: 配置落盘与容器重启编排
# ---------------------------------------------------------------------------


class _FakeProc:
    """伪装 asyncio subprocess，用于验证 docker 降级分支。"""

    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """伪装 httpx.AsyncClient，按脚本依次返回结果/抛异常。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, timeout=None):
        self.calls.append(url)
        item = self._script.pop(0) if self._script else self._last
        self._last = item
        if isinstance(item, Exception):
            raise item
        return item


class TestWriteConfigToDisk:
    def test_writes_config_and_creates_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "viking"
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(target))

        openviking.write_config_to_disk(REAL_CONFIG)

        config_file = target / "ov.conf"
        assert config_file.read_text(encoding="utf-8") == REAL_CONFIG
        # 容器以只读方式挂载，需保证其他用户可读
        assert oct(config_file.stat().st_mode & 0o777) == "0o644"

    def test_written_content_hash_matches_entrypoint(self, tmp_path, monkeypatch):
        """落盘内容的 sha256 必须与 compute_content_hash 一致，
        否则 entrypoint.sh 的哈希比对会认为配置未变化而跳过重建。"""
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(tmp_path))
        openviking.write_config_to_disk(REAL_CONFIG)

        on_disk = (tmp_path / "ov.conf").read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == compute_content_hash(REAL_CONFIG)

    def test_preserves_inode_on_rewrite(self, tmp_path, monkeypatch):
        """ov.conf 是单文件 bind mount，换 inode 会让容器继续读到旧文件。"""
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(tmp_path))
        config_file = tmp_path / "ov.conf"

        openviking.write_config_to_disk(REAL_CONFIG)
        first_inode = config_file.stat().st_ino

        updated = json.dumps({"embedding": {"dense": {"dimension": 3072}}})
        openviking.write_config_to_disk(updated)

        assert config_file.stat().st_ino == first_inode
        assert config_file.read_text(encoding="utf-8") == updated

    def test_backs_up_previous_config(self, tmp_path, monkeypatch):
        """entrypoint.sh 校验失败时会从 .ov_conf_backup 回滚。"""
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(tmp_path))

        openviking.write_config_to_disk(REAL_CONFIG)
        assert not (tmp_path / ".ov_conf_backup").exists()  # 首次写入无旧配置

        updated = json.dumps({"embedding": {"dense": {"dimension": 3072}}})
        openviking.write_config_to_disk(updated)

        backup = tmp_path / ".ov_conf_backup"
        assert backup.read_text(encoding="utf-8") == REAL_CONFIG

    def test_rejects_invalid_json_before_writing(self, tmp_path, monkeypatch):
        """非法 JSON 会让 lion-viking 起不来，必须写盘前拦截。"""
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(tmp_path))

        with pytest.raises(CustomError) as exc:
            openviking.write_config_to_disk("embedding.dimension = 1536")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON
        assert not (tmp_path / "ov.conf").exists()

    def test_does_not_clobber_existing_config_on_invalid_input(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(tmp_path))
        openviking.write_config_to_disk(REAL_CONFIG)

        with pytest.raises(CustomError):
            openviking.write_config_to_disk("{not json")

        assert (tmp_path / "ov.conf").read_text(encoding="utf-8") == REAL_CONFIG

    def test_os_error_maps_to_apply_failed(self, tmp_path, monkeypatch):
        """挂载缺失/只读文件系统等落盘失败要转成明确错误码。"""
        monkeypatch.setattr(openviking, "OPENVIKING_CONFIG_DIR", str(tmp_path))

        def _boom(*args, **kwargs):
            raise PermissionError("read-only file system")

        monkeypatch.setattr(openviking.Path, "mkdir", _boom)

        with pytest.raises(CustomError) as exc:
            openviking.write_config_to_disk(REAL_CONFIG)
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_APPLY_FAILED


class TestRestartOpenVikingContainer:
    @pytest.mark.asyncio
    async def test_prefers_supervisor(self, monkeypatch):
        monkeypatch.setattr(
            openviking.supervisor, "restart_process", lambda name: True
        )

        def _no_docker(*args, **kwargs):
            raise AssertionError("supervisor 成功后不应再调 docker")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_docker)

        assert await openviking.restart_openviking_container() is None

    @pytest.mark.asyncio
    async def test_falls_back_to_docker(self, monkeypatch):
        monkeypatch.setattr(
            openviking.supervisor, "restart_process", lambda name: False
        )
        seen = {}

        async def _fake_exec(*args, **kwargs):
            seen["args"] = args
            return _FakeProc(0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        await openviking.restart_openviking_container()
        assert seen["args"][:2] == ("docker", "restart")
        assert seen["args"][2] == openviking.OPENVIKING_CONTAINER_NAME

    @pytest.mark.asyncio
    async def test_supervisor_exception_falls_back(self, monkeypatch):
        def _raise(name):
            raise ConnectionRefusedError("supervisor down")

        monkeypatch.setattr(openviking.supervisor, "restart_process", _raise)

        async def _fake_exec(*args, **kwargs):
            return _FakeProc(0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        await openviking.restart_openviking_container()

    @pytest.mark.asyncio
    async def test_raises_when_both_paths_fail(self, monkeypatch):
        monkeypatch.setattr(
            openviking.supervisor, "restart_process", lambda name: False
        )

        async def _fake_exec(*args, **kwargs):
            return _FakeProc(1, b"No such container: lion-viking")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        with pytest.raises(CustomError) as exc:
            await openviking.restart_openviking_container()
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_RESTART_FAILED

    @pytest.mark.asyncio
    async def test_missing_docker_cli_raises_restart_failed(self, monkeypatch):
        """lion_mgmt 镜像里没有 docker CLI，也没挂 docker.sock。"""
        monkeypatch.setattr(
            openviking.supervisor, "restart_process", lambda name: False
        )

        async def _fake_exec(*args, **kwargs):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        with pytest.raises(CustomError) as exc:
            await openviking.restart_openviking_container()
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_RESTART_FAILED


class TestWaitForOpenVikingReady:
    @pytest.mark.asyncio
    async def test_returns_when_healthy(self, monkeypatch):
        client = _FakeAsyncClient([_FakeResponse(200)])
        monkeypatch.setattr(openviking.httpx, "AsyncClient", lambda **kw: client)

        await openviking.wait_for_openviking_ready(timeout=5)
        assert client.calls == [f"{openviking.OPENVIKING_ADMIN_BASE_URL}/health"]

    @pytest.mark.asyncio
    async def test_polls_until_ready(self, monkeypatch):
        """重启窗口内连接被拒是预期现象，应继续轮询。"""
        client = _FakeAsyncClient(
            [
                ConnectionError("connection refused"),
                _FakeResponse(503),
                _FakeResponse(200),
            ]
        )
        monkeypatch.setattr(openviking.httpx, "AsyncClient", lambda **kw: client)
        monkeypatch.setattr(openviking, "HEALTH_POLL_INTERVAL", 0)

        await openviking.wait_for_openviking_ready(timeout=5)
        assert len(client.calls) == 3

    @pytest.mark.asyncio
    async def test_timeout_raises_not_ready(self, monkeypatch):
        client = _FakeAsyncClient([ConnectionError("refused")])
        monkeypatch.setattr(openviking.httpx, "AsyncClient", lambda **kw: client)
        monkeypatch.setattr(openviking, "HEALTH_POLL_INTERVAL", 0)

        with pytest.raises(CustomError) as exc:
            await openviking.wait_for_openviking_ready(timeout=0)
        assert exc.value.enum == ErrorCode.EMBEDDING_SERVICE_NOT_READY

    @pytest.mark.asyncio
    async def test_skips_when_base_url_unset(self, monkeypatch):
        monkeypatch.setattr(openviking, "OPENVIKING_ADMIN_BASE_URL", "")

        def _no_client(**kwargs):
            raise AssertionError("未配置地址时不应发起请求")

        monkeypatch.setattr(openviking.httpx, "AsyncClient", _no_client)
        await openviking.wait_for_openviking_ready(timeout=5)

    @pytest.mark.asyncio
    async def test_defaults_to_configured_timeout(self, monkeypatch):
        client = _FakeAsyncClient([_FakeResponse(200)])
        monkeypatch.setattr(openviking.httpx, "AsyncClient", lambda **kw: client)
        await openviking.wait_for_openviking_ready()
        assert client.calls
