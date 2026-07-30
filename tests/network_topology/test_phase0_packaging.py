from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
DOCKERFILE = REPOSITORY_ROOT / "Dockerfile"
SCHEMAS_COPY = (
    "COPY services/network_topology/schemas/ ./services/network_topology/schemas/"
)
REGISTRIES_COPY = (
    "COPY services/network_topology/registries/ ./services/network_topology/registries/"
)
CONTRACT_SMOKE = "RUN python -m services.network_topology.contract_smoke"


def runtime_stage_lines() -> list[str]:
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip().upper().endswith(" AS RUNTIME")
    )
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start=start + 1)
            if line.strip().upper().startswith("FROM ")
        ),
        len(lines),
    )
    return [line.strip() for line in lines[start + 1 : end]]


def test_runtime_stage_copies_contract_resources_before_smoke() -> None:
    lines = runtime_stage_lines()
    schemas_index = lines.index(SCHEMAS_COPY)
    registries_index = lines.index(REGISTRIES_COPY)
    smoke_index = lines.index(CONTRACT_SMOKE)

    assert schemas_index < smoke_index
    assert registries_index < smoke_index
    assert smoke_index < lines.index('ENTRYPOINT ["./docker-entrypoint.sh"]')


def test_contract_resources_are_direct_context_copies() -> None:
    copy_lines = [line for line in runtime_stage_lines() if line.startswith("COPY ")]

    assert copy_lines.count(SCHEMAS_COPY) == 1
    assert copy_lines.count(REGISTRIES_COPY) == 1
    assert not any(
        "--from=" in line and "services/network_topology" in line for line in copy_lines
    )
