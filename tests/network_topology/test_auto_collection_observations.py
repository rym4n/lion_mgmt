import orjson
import pytest

from services.network_topology import canonicalization, contracts
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.observations import parse_action_output, raw_observation, typed_observations
from services.network_topology.redaction import scrub_action_output


def test_scrub_removes_credential_keys_and_lines():
    cleaned = scrub_action_output({"password": "x", "data": {"text": "sysname A\nsnmp-agent community read public\nvlan 10"}})
    assert "password" not in cleaned
    assert "public" not in orjson.dumps(cleaned).decode()
    canonicalization.reject_credential_blocks(cleaned)


def test_parse_rejects_truncated_and_non_json():
    with pytest.raises(CollectionError, match="COLLECTION_OUTPUT_TRUNCATED"):
        parse_action_output('{"a": 1}...(已截断，原始 2000000 字节，仅保留前 262144 字节)')
    with pytest.raises(CollectionError, match="COLLECTION_OUTPUT_NOT_JSON"):
        parse_action_output("not json")


def test_raw_observation_is_contract_valid():
    obs = raw_observation(observation_id="item-1:res-1", app_name="huawei_switch", action_func="collect_topology_observations_v2", app_version="v", resource_id="res-1", output={"code": 200, "data": {"records": []}}, observed_at="2026-10-14T00:00:00Z")
    contracts.validate_contract(obs, "aioe.network-topology.observation/raw.action_output/v1")
    canonicalization.validate_observation_hashes(obs)


def test_cli_text_records_do_not_become_typed():
    output = {"data": {"records": [{"kinds": ["system.identity"], "content_type": "text/plain", "data": "Huawei VRP"}]}}
    assert typed_observations(output, resource_id="r", item_id="i", observed_at="2026-10-14T00:00:00Z") == []
