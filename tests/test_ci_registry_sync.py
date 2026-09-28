# coding=utf-8
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

PYTHON_IMAGE = (
    "public.ecr.aws/docker/library/python:3.11-slim@"
    "sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7"
)
DEBIAN_IMAGE = (
    "public.ecr.aws/docker/library/debian:bookworm-slim@@"
    "sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241"
)


def test_ci_uses_pinned_fast_registry_images_and_remote_build_cache():
    buildspec = yaml.safe_load(
        (ROOT / ".onedev-buildspec.yml").read_text(encoding="utf-8")
    )
    ci_job = next(job for job in buildspec["jobs"] if job["name"] == "ci")
    command_steps = [step for step in ci_job["steps"] if step["type"] == "CommandStep"]
    build_step = next(
        step for step in ci_job["steps"] if step["type"] == "BuildImageStep"
    )

    assert {step["image"] for step in command_steps} == {DEBIAN_IMAGE}
    assert build_step["moreOptions"] == (
        "--cache-from=type=registry,ref=10.3.0.41:5000/lion_mgmt:buildcache "
        "--cache-to=type=registry,ref=10.3.0.41:5000/lion_mgmt:buildcache,mode=min"
    )
    assert build_step["platforms"] == "linux/amd64,linux/arm64"
    assert "mode=max" not in build_step["moreOptions"]

    sync_job = next(job for job in buildspec["jobs"] if job["name"] == "同步镜像")
    sync_command = sync_job["steps"][0]["interpreter"]["commands"]
    assert (
        "gcrane copy 10.3.0.41:5000/lion_mgmt:@tag@ "
        "10.3.0.38:5000/lion_mgmt:@tag@ --insecure"
    ) in sync_command
    assert sync_job["jobDependencies"] == [
        {"jobName": "ci", "requireSuccessful": True, "artifacts": "**"}
    ]


def test_dockerfile_avoids_docker_hub_and_keeps_runtime_packages_cacheable():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count(f"FROM {PYTHON_IMAGE}") == 2
    assert "FROM python:" not in dockerfile
    assert "FROM timberio/vector:" not in dockerfile
    assert "ARG VECTOR_VERSION=0.50.0" in dockerfile
    assert (
        "ARG VECTOR_AMD64_SHA256="
        "5ecc335a2d2a1ff73c8408b5d979b6e238f5663ba8803ea5054ca8aa464dd410"
    ) in dockerfile
    assert (
        "ARG VECTOR_ARM64_SHA256="
        "d2732550f5e35ab09200e87d4965391b9f90510bc0055cfe37548fe4c12bc891"
    ) in dockerfile
    assert (
        'amd64) VECTOR_ARCH="x86_64"; '
        'VECTOR_SHA256="$VECTOR_AMD64_SHA256" ;;'
    ) in dockerfile
    assert (
        'arm64) VECTOR_ARCH="aarch64"; '
        'VECTOR_SHA256="$VECTOR_ARM64_SHA256" ;;'
    ) in dockerfile
    assert 'echo "Unsupported TARGETARCH: $TARGETARCH"' in dockerfile
    assert "https://packages.timber.io/vector/${VECTOR_VERSION}/" in dockerfile
    assert "vector-${VECTOR_VERSION}-${VECTOR_ARCH}-unknown-linux-gnu.tar.gz" in dockerfile
    assert "sha256sum -c -" in dockerfile

    runtime_start = dockerfile.index(f"FROM {PYTHON_IMAGE} AS runtime")
    runtime_copy = dockerfile.index("COPY --from=builder", runtime_start)
    runtime_packages = dockerfile.index("libreoffice-writer", runtime_start)
    runtime_vector = dockerfile.index("/usr/bin/vector", runtime_start)
    assert runtime_packages < runtime_copy
    assert runtime_vector < runtime_copy
    assert "apt-get" not in dockerfile[runtime_copy:]
