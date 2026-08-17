# coding=utf-8
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_lion_mgmt_image_is_synced_to_packaging_registry():
    buildspec = yaml.safe_load(
        (ROOT / ".onedev-buildspec.yml").read_text(encoding="utf-8")
    )
    jobs = {job["name"]: job for job in buildspec["jobs"]}

    sync_job = jobs["同步镜像"]
    assert sync_job["jobExecutor"] == "tgy-deploy"
    assert sync_job["jobDependencies"] == [
        {
            "jobName": "ci",
            "requireSuccessful": True,
            "artifacts": "**",
        }
    ]

    command = sync_job["steps"][0]["interpreter"]["commands"]
    assert "10.3.0.41:5000/lion_mgmt:@tag@" in command
    assert "10.3.0.38:5000/lion_mgmt:@tag@" in command
    assert "gcrane copy" in command
