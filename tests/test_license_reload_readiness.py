import asyncio
import hashlib
import inspect
from types import SimpleNamespace

import orjson

from services.license import views


def _json_body(response):
    return orjson.loads(response.body)


def test_license_upload_returns_target_snapshot_and_replaces_file_atomically(
    monkeypatch, tmp_path
):
    old_file = tmp_path / "old.lic"
    old_file.write_bytes(b"old-license")
    file_content = b"new-license"
    license_data = {
        "moduleList": [
            {
                "module": "digital_employee_bp",
                "auth": True,
                "expireTime": 4102444800000,
            },
            {
                "module": "unsupported_bp",
                "auth": True,
                "expireTime": 4102444800000,
            },
        ]
    }
    restart_calls = []
    app = SimpleNamespace(
        ctx=SimpleNamespace(
            supported_license_modules={"digital_employee_bp", "command_bp"}
        ),
        m=SimpleNamespace(
            restart=lambda **kwargs: restart_calls.append(kwargs)
        ),
    )
    request = SimpleNamespace(
        files={"file": SimpleNamespace(body=file_content)},
        app=app,
    )
    monkeypatch.setattr(views, "LICENSE_DIR", str(tmp_path))
    monkeypatch.setattr(views, "verify_license", lambda _content: license_data)

    response = asyncio.run(inspect.unwrap(views.verify_license_api)(request))

    payload = _json_body(response)["data"]
    assert payload == {
        "message": "上传成功，正在加载配置文件，请稍后！",
        "license_sha256": hashlib.sha256(file_content).hexdigest(),
        "required_modules": ["digital_employee_bp"],
    }
    assert restart_calls == [{"zero_downtime": True, "all_workers": True}]
    license_files = list(tmp_path.glob("*.lic"))
    assert [path.name for path in license_files] == [
        hashlib.md5(file_content).hexdigest() + ".lic"
    ]
    assert license_files[0].read_bytes() == file_content
    assert not list(tmp_path.glob("*.tmp"))


def test_reload_status_reports_current_worker_snapshot():
    request = SimpleNamespace(
        app=SimpleNamespace(
            ctx=SimpleNamespace(
                worker_ready=True,
                active_license_sha256="sha-new",
                registered_license_modules={"digital_employee_bp", "command_bp"},
            )
        )
    )

    response = asyncio.run(
        inspect.unwrap(views.get_license_reload_status)(request)
    )

    assert _json_body(response)["data"] == {
        "ready": True,
        "license_sha256": "sha-new",
        "registered_modules": ["command_bp", "digital_employee_bp"],
    }


def test_worker_license_helpers_do_not_read_new_disk_license(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            ctx=SimpleNamespace(
                license_data={"company": "worker-snapshot"},
                active_license_sha256="worker-sha",
            )
        )
    )
    monkeypatch.setattr(
        views, "load_license", lambda: {"company": "new-disk-license"}
    )
    monkeypatch.setattr(views, "get_license_sha256", lambda: "new-disk-sha")

    assert views._worker_license_data(request) == {"company": "worker-snapshot"}
    assert views._worker_license_sha256(request) == "worker-sha"
