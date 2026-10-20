from copy import deepcopy

from services.network_access_control.collection import validation
from tests.network_access_control.policy_agent_fixtures import RAW_OUTPUTS, document, observations, policy


def check(doc, resource_id="r1", max_rules=20000):
    return validation.validate_document(doc, resource_id=resource_id,
                                        observations=observations(resource_id), max_rules=max_rules)


def test_valid_multiline_ssh_rule_passes():
    assert check(document()) == []


def test_api_object_raw_text_matches_serialized_output():
    doc = document("r2", [policy(policy_key="7", rule_name="dns", raw_text='{"policyid": 7, "name": "dns", "action": "accept"}')])
    assert check(doc, "r2") == []


def test_schema_violation_reported():
    doc = document(); doc["policies"][0]["action"] = "allow"
    assert check(doc)


def test_resource_mismatch_rejected():
    assert check(document("r2"), "r1")


def test_duplicate_policy_key_rejected():
    assert check(document(policies=[policy(), policy()]))


def test_fabricated_raw_text_rejected():
    errors = check(document(policies=[policy(raw_text="rule name evil action permit source 6.6.6.6")]))
    assert any("raw_text" in e for e in errors)


def test_multiline_ssh_rule_with_indentation_and_crlf_passes():
    raw = deepcopy(RAW_OUTPUTS)
    text = "rule name allow-ssh\r\n  source-zone trust\r\n  destination-zone dmz\r\n  service ssh\r\n  action permit"
    raw["r1"][0]["output"]["outputs"][0]["content"] = "security-policy\r\n " + text + "\r\nreturn"
    from services.network_access_control.collection.agent_jobs import observations_for
    doc = document(policies=[policy(policy_key="allow-ssh", rule_name="allow-ssh",
                                    raw_text="rule name allow-ssh\n source-zone trust\n destination-zone dmz\n service ssh\n action permit")])
    assert validation.validate_document(doc, resource_id="r1", observations=observations_for(raw, "r1"), max_rules=20000) == []


def test_match_cannot_span_two_segments():
    from services.network_access_control.collection.agent_jobs import observations_for
    raw = {"r1": [{"item_id": "i1", "app_name": "a", "action_func": "f", "app_version": "1",
                   "output": {"outputs": [{"source": "x", "content": "rule name a"}, {"source": "y", "content": "action permit"}]}}]}
    leaves = validation.evidence_haystack(observations_for(raw, "r1"))
    assert "\x00" in leaves
    doc = document(policies=[policy(raw_text="rule name a action permit")])
    assert validation.validate_document(doc, resource_id="r1", observations=observations_for(raw, "r1"), max_rules=20000)


def test_raw_text_containing_nul_rejected():
    assert check(document(policies=[policy(raw_text="rule\x00name")]))


def test_whitespace_only_raw_text_rejected():
    assert check(document(policies=[policy(raw_text="   \n ")]))


def test_rule_limit_enforced():
    rules = [policy(policy_key=f"k{i}") for i in range(3)]
    assert check(document(policies=rules), max_rules=2)


def test_non_object_document_rejected():
    assert check(["not", "a", "doc"])


def test_schema_file_is_draft_2020_12():
    assert validation.document_schema()["$id"] == "https://schemas.aioe.local/aioe.network-policy.normalized/v1"
