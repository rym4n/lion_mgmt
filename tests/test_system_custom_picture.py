# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.system_config import views


class _PictureConfig:
    def __init__(self, value):
        self.value = value
        self.deleted = False

    async def delete(self):
        self.deleted = True


def _run_delete_picture(monkeypatch, pic_type, config, *, storage_deleted=True):
    deleted_objects = []

    async def get_or_none(**kwargs):
        assert kwargs == {"key": pic_type, "module": "system"}
        return config

    async def delete_from_s3(bucket, filename, prefix):
        deleted_objects.append((bucket, filename, prefix))
        return storage_deleted

    monkeypatch.setattr(views.Config, "get_or_none", staticmethod(get_or_none))
    monkeypatch.setattr(views, "delete_from_s3", delete_from_s3)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(args={"type": pic_type})
    payload = asyncio.run(inspect.unwrap(views.delete_picture)(request))
    return payload, deleted_objects


def _run_get_picture(monkeypatch, pic_type, config):
    async def get_or_none(**kwargs):
        assert kwargs == {"key": pic_type, "module": "system"}
        return config

    monkeypatch.setattr(views.Config, "get_or_none", staticmethod(get_or_none))
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(args={"type": pic_type})
    return asyncio.run(inspect.unwrap(views.get_bgpic)(request))


def test_picture_payload_exposes_default_logo_and_customization_state():
    assert views._picture_payload("logo") == {
        "type": "logo",
        "url": "/logo.svg",
        "customized": False,
    }
    assert views._picture_payload("background", "/files/user/custom/bg.png") == {
        "type": "background",
        "url": "/files/user/custom/bg.png",
        "customized": True,
    }


def test_get_picture_returns_default_logo_when_no_customization_exists(monkeypatch):
    assert _run_get_picture(monkeypatch, "logo", None) == {
        "type": "logo",
        "url": "/logo.svg",
        "customized": False,
    }


def test_get_picture_returns_saved_background(monkeypatch):
    config = _PictureConfig("/files/user/custom/bg.webp")

    assert _run_get_picture(monkeypatch, "background", config) == {
        "type": "background",
        "url": "/files/user/custom/bg.webp",
        "customized": True,
    }


@pytest.mark.parametrize(
    ("pic_type", "url", "filename"),
    [
        ("background", "/files/user/custom/bg.png", "bg.png"),
        ("logo", "https://example.test/files/user/custom/logo.svg?v=1", "logo.svg"),
        ("logo", "/files/user/tools/logo.svg", None),
        ("logo", "/files/user/custom/bg.svg", None),
        ("logo", "/files/user/custom/nested/logo.svg", None),
    ],
)
def test_custom_picture_filename_only_accepts_owned_objects(
    pic_type, url, filename
):
    assert views._custom_picture_filename(pic_type, url) == filename


def test_delete_picture_removes_config_and_owned_object(monkeypatch):
    config = _PictureConfig("/files/user/custom/logo.svg")

    payload, deleted_objects = _run_delete_picture(monkeypatch, "logo", config)

    assert config.deleted is True
    assert deleted_objects == [("user", "logo.svg", "custom")]
    assert payload == {
        "type": "logo",
        "url": "/logo.svg",
        "customized": False,
    }


def test_delete_picture_is_idempotent_when_no_customization_exists(monkeypatch):
    payload, deleted_objects = _run_delete_picture(monkeypatch, "background", None)

    assert deleted_objects == []
    assert payload == {"type": "background", "url": "", "customized": False}


def test_delete_picture_restores_default_when_object_cleanup_fails(monkeypatch):
    config = _PictureConfig("/files/user/custom/bg.png")

    payload, deleted_objects = _run_delete_picture(
        monkeypatch,
        "background",
        config,
        storage_deleted=False,
    )

    assert config.deleted is True
    assert deleted_objects == [("user", "bg.png", "custom")]
    assert payload == {"type": "background", "url": "", "customized": False}


def test_delete_picture_rejects_unknown_type(monkeypatch):
    request = SimpleNamespace(args={"type": "avatar"})

    with pytest.raises(CustomError) as error:
        asyncio.run(inspect.unwrap(views.delete_picture)(request))

    assert error.value.enum == ErrorCode.PARAM_ERROR
