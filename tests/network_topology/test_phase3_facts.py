from copy import deepcopy

import pytest

from services.network_topology.parsers.base import CandidateBatch
from services.network_topology.contracts import validate_contract
from services.network_topology.facts.candidate_conversion import (
    CandidateConversionError,
    convert_candidate_batch,
    convert_candidates,
)
from services.network_topology.facts.merge import FactMergeError, merge_fact_results
from services.network_topology.facts.validation import (
    EvidenceValidationError,
    validate_relation_evidence,
)


def _observation(data="hostname edge-1\ninterface Gi0/1\n lldp neighbor core-2 Gi0/2"):
    return {"observation_id": "obs-1", "resource_id": "r1",
            "content_sha256": "sha256:" + "2" * 64, "data": data,
            "fact_plane": "configured", "kind": "legacy.running_config"}


def _candidates():
    empty = {name: [] for name in (
        "devices", "device_groups", "network_contexts", "interfaces", "segments",
        "adjacencies", "routes", "routing_relationships", "tunnels", "memberships",
        "gateways", "security_boundaries", "wan_connections", "unresolved_references",
        "warnings",
    )}
    empty["devices"] = [{
        "candidate_id": "chunk:device-1", "device_type": "router",
        "identity_aliases": [{"type": "hostname", "namespace": "dns", "value": "edge-1"}],
        "roles": ["edge"], "inventory_state": "managed",
        "evidence_refs": [{"observation_id": "obs-1",
                           "locator": {"type": "lines", "start": 1, "end": 1}}],
    }]
    return {"schema_version": "aioe.network-topology.llm-fact-candidates/v1", **empty}


def test_candidate_conversion_binds_server_ids_and_observed_provenance():
    summary = {"provider": "openai", "model": "allowed-model",
               "prompt_version": "aioe.network-topology.prompt/fact-candidates/v1",
               "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
               "attempts": 1, "elapsed_ms": 20}
    result = convert_candidates(
        _candidates(), snapshot_id="snapshot-1", analysis_run_id="run-1",
        resource_id="r1", chunk_id="chunk", input_hash="sha256:" + "3" * 64,
        observations=(_observation(),), llm_summary=summary,
    )
    fact = result.facts["devices"][0]
    assert fact["id"] == "device:r1"
    assert fact["source_candidate_ids"] == ["chunk:device-1"]
    assert fact["resource_ids"] == ["r1"]
    provenance = result.facts["provenance_catalog"][0]
    assert provenance["provenance"] == {
        "type": "observed", "resource_id": "r1", "observation_id": "obs-1",
        "redacted_sha256": "sha256:" + "2" * 64,
        "locator": {"type": "lines", "start": 1, "end": 1},
    }
    validate_contract(result.facts, "aioe.network-topology.facts/v1")
    merged = merge_fact_results(
        (result,), expected_resource_ids=("r1",), required_chunk_ids=("chunk",),
        successful_chunk_ids=("chunk",), manifest_non_empty=True,
    )
    assert merged.validation_report["llm_summaries"] == [summary]
    assert "raw_output" not in merged.validation_report


def test_candidate_schema_and_same_immutable_observation_fail_closed():
    invalid = _candidates()
    invalid["devices"][0]["x"] = 10
    with pytest.raises(CandidateConversionError, match="schema"):
        convert_candidates(invalid, snapshot_id="s", analysis_run_id="a",
                           resource_id="r1", chunk_id="chunk",
                           input_hash="sha256:" + "3" * 64,
                           observations=(_observation(),))

    wrong = _candidates()
    wrong["devices"][0]["evidence_refs"][0]["observation_id"] = "obs-other"
    with pytest.raises(CandidateConversionError, match="observation"):
        convert_candidates(wrong, snapshot_id="s", analysis_run_id="a",
                           resource_id="r1", chunk_id="chunk",
                           input_hash="sha256:" + "3" * 64,
                           observations=(_observation(),))


@pytest.mark.parametrize(('address', 'prefix', 'network'), [
    ('192.0.2.17', 24, '192.0.2.0/24'),
    ('2001:db8::17', 64, '2001:db8::/64'),
])
def test_segment_evidence_can_prove_network_with_interface_address_and_prefix(address, prefix, network):
    candidates = _candidates()
    evidence = [{'observation_id': 'obs-1',
                 'locator': {'type': 'json_pointer', 'pointer': '/interface'}}]
    candidates['devices'][0].update(identity_aliases=[], evidence_refs=evidence)
    candidates['segments'] = [{
        'candidate_id': 'chunk:network', 'canonical_cidr': network,
        'device_candidate_ref': 'chunk:device-1', 'context_candidate_ref': None,
        'vlan': None, 'vni': None, 'gateway_selectors': [{'candidate_ref': 'chunk:device-1'}],
        'evidence_refs': evidence,
    }]
    observation = _observation({'interface': {'addresses': [
        {'ipaddress': {'start': address, 'bits': prefix}}
    ]}})
    result = convert_candidates(
        candidates, snapshot_id='s', analysis_run_id='a', resource_id='r1',
        chunk_id='chunk', input_hash='sha256:' + '3' * 64, observations=(observation,),
    )
    assert result.facts['segments'][0]['attributes']['canonical_cidr'] == network

    # A different network or missing prefix is still unsupported.
    for value in [{'start': address, 'bits': prefix + 1}, {'start': address}]:
        observation['data']['interface']['addresses'][0]['ipaddress'] = value
        with pytest.raises(CandidateConversionError, match='does not support'):
            convert_candidates(
                candidates, snapshot_id='s', analysis_run_id='a', resource_id='r1',
                chunk_id='chunk', input_hash='sha256:' + '3' * 64, observations=(observation,),
            )

@pytest.mark.parametrize(("relation_type", "evidence", "code"), [
    ("physical", {"same_subnet": True}, "PHYSICAL_NEIGHBOR_EVIDENCE_REQUIRED"),
    ("l2", {"local_trunk": True}, "BIDIRECTIONAL_L2_EVIDENCE_REQUIRED"),
    ("l2", {"local_vlan": 10, "remote_vlan": 20, "local_trunk": True,
            "remote_trunk": True}, "L2_EVIDENCE_CONFLICT"),
    ("routing", {"protocol": "bgp", "session_state": "down"},
     "ROUTING_SESSION_NOT_OPERATIONAL"),
    ("tunnel", {"local_context": "vrf-a", "remote_context": "vrf-b",
                "local_endpoint": "1.1.1.1", "remote_endpoint": "2.2.2.2"},
     "TUNNEL_CONTEXT_MISMATCH"),
])
def test_relation_matrix_rejects_weak_or_conflicting_evidence(
    relation_type, evidence, code,
):
    with pytest.raises(EvidenceValidationError) as error:
        validate_relation_evidence(relation_type, evidence)
    assert error.value.code == code


@pytest.mark.parametrize(("relation_type", "evidence"), [
    ("physical", {"protocol": "lldp", "local_port": "Gi0/1", "remote_port": "Gi0/2"}),
    ("l2", {"local_trunk": True, "remote_trunk": True, "local_vlan": 10,
            "remote_vlan": 10}),
    ("l3", {"protocol": "arp", "local_address": "10.0.0.1",
            "remote_address": "10.0.0.2", "context": "vrf-a"}),
    ("routing", {"protocol": "bgp", "session_state": "up", "vrf": "vrf-a"}),
    ("tunnel", {"local_context": "vrf-a", "remote_context": "vrf-a",
                "local_endpoint": "1.1.1.1", "remote_endpoint": "2.2.2.2"}),
    ("gateway", {"fhrp_protocol": "vrrp", "virtual_address": "10.0.0.1"}),
    ("security", {"device_id": "d1", "interface_id": "i1", "zone_id": "z1"}),
    ("wan", {"connection_type": "circuit", "authority_validated": True,
             "circuit_id": "C-1", "source_revision": "rev-1"}),
])
def test_relation_matrix_accepts_minimum_evidence(relation_type, evidence):
    assert validate_relation_evidence(relation_type, evidence) == evidence


def test_security_boundary_never_implies_zone_to_zone_permission():
    evidence = {"device_id": "d1", "interface_id": "i1", "zone_id": "z1",
                "allowed_zone": "z2"}
    with pytest.raises(EvidenceValidationError) as error:
        validate_relation_evidence("security", evidence)
    assert error.value.code == "SECURITY_POLICY_INFERENCE_FORBIDDEN"


def test_merge_fails_closed_for_missing_devices_chunks_and_provenance():
    converted = convert_candidates(
        _candidates(), snapshot_id="snapshot-1", analysis_run_id="run-1",
        resource_id="r1", chunk_id="chunk", input_hash="sha256:" + "3" * 64,
        observations=(_observation(),),
    )
    merged = merge_fact_results(
        (converted,), expected_resource_ids=("r1",),
        required_chunk_ids=("chunk",), successful_chunk_ids=("chunk",),
        manifest_non_empty=True,
    )
    assert merged.facts["devices"][0]["id"] == "device:r1"
    assert merged.validation_report["unresolved"] == []
    assert "candidates" not in merged.validation_report

    for kwargs in (
        {"expected_resource_ids": ("r1", "r2")},
        {"required_chunk_ids": ("chunk", "missing")},
    ):
        options = {"expected_resource_ids": ("r1",),
                   "required_chunk_ids": ("chunk",),
                   "successful_chunk_ids": ("chunk",), "manifest_non_empty": True}
        options.update(kwargs)
        with pytest.raises(FactMergeError):
            merge_fact_results((converted,), **options)

    no_provenance = deepcopy(converted.facts)
    no_provenance["provenance_catalog"] = []
    with pytest.raises(FactMergeError, match="provenance"):
        merge_fact_results((no_provenance,), expected_resource_ids=("r1",),
                           required_chunk_ids=("chunk",),
                           successful_chunk_ids=("chunk",), manifest_non_empty=True)


def test_host_is_excluded_unless_authoritative_profile_exception():
    candidates = _candidates()
    candidates["devices"][0]["device_type"] = "host"
    result = convert_candidates(
        candidates, snapshot_id="s", analysis_run_id="a", resource_id="r1",
        chunk_id="chunk", input_hash="sha256:" + "3" * 64,
        observations=(_observation(),),
    )
    assert result.facts["devices"] == []


def test_authoritative_circuit_preserves_full_source_provenance():
    provenance = {
        "id": "authority:circuit:C-1",
        "provenance": {"type": "authoritative", "source_system": "cmdb",
                       "source_record_id": "circuit:C-1", "source_revision": "rev-7",
                       "as_of": "2026-07-30T00:00:00Z", "field_locator": "/circuits/C-1"},
    }
    wan = {"id": "wan:C-1", "fact_family": "wan", "resource_ids": ["r1"],
           "source_candidate_ids": [],
           "attributes": {"connection_type": "circuit", "local_id": "device:r1",
                          "remote_id": "network:remote", "provider": "carrier",
                          "circuit_id": "C-1", "transport": "private_circuit",
                          "state": "unknown"},
           "provenance_ids": [provenance["id"]], "parsing_state": "verified"}
    batch = CandidateBatch(
        parser_id="registered", parser_version=1, chunk_id="chunk",
        input_hash="sha256:" + "3" * 64, resource_id="r1",
        candidates={"wan_connections": [wan], "provenance_catalog": [provenance]},
    )
    result = convert_candidate_batch(
        batch, snapshot_id="s", analysis_run_id="a", resource_id="r1",
        chunk_id="chunk", input_hash="sha256:" + "3" * 64,
        observations=(_observation(),),
        authoritative_context={"circuits": [{"provenance": provenance["provenance"]}]},
    )
    assert result.facts["wan_connections"] == [wan]
    assert result.facts["provenance_catalog"] == [provenance]
    with pytest.raises(CandidateConversionError, match="outside context"):
        convert_candidate_batch(
            batch, snapshot_id="s", analysis_run_id="a", resource_id="r1",
            chunk_id="chunk", input_hash="sha256:" + "3" * 64,
            observations=(_observation(),), authoritative_context={},
        )


def test_security_binding_keeps_configured_and_operational_state_separate():
    candidates = _candidates()
    evidence = [{"observation_id": "obs-1",
                 "locator": {"type": "lines", "start": 2, "end": 4}}]
    candidates["network_contexts"] = [{
        "candidate_id": "chunk:zone", "owning_candidate_ref": "chunk:device-1",
        "kind": "zone", "local_id": "trust", "name": "trust",
        "namespace_attributes": {}, "evidence_refs": evidence,
    }]
    candidates["interfaces"] = [{
        "candidate_id": "chunk:if1", "device_candidate_ref": "chunk:device-1",
        "context_candidate_ref": None, "canonical_name": "Gi0/1", "raw_name": "Gi0/1",
        "if_index": None, "mac": None, "parent_ref": None, "lag_ref": None,
        "addresses": [], "configured_state": "up", "operational_state": "down",
        "evidence_refs": evidence,
    }]
    candidates["security_boundaries"] = [{
        "candidate_id": "chunk:security", "device_candidate_ref": "chunk:device-1",
        "interface_candidate_ref": "chunk:if1", "zone_context_ref": "chunk:zone",
        "evidence_refs": evidence,
    }]
    observation = _observation(
        "hostname edge-1\ninterface Gi0/1\n shutdown\nzone trust interface Gi0/1"
    )
    result = convert_candidates(
        candidates, snapshot_id="s", analysis_run_id="a", resource_id="r1",
        chunk_id="chunk", input_hash="sha256:" + "3" * 64,
        observations=(observation,),
    )
    interface = result.facts["interfaces"][0]["attributes"]
    security = result.facts["security_boundaries"][0]["attributes"]
    assert (interface["configured_state"], interface["operational_state"]) == ("up", "down")
    assert (security["configured_state"], security["operational_state"]) == (
        "configured", "unknown"
    )
