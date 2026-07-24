# coding=utf-8
from services.supply_chain_review import manager


def test_access_control_keyword_search_matches_nested_policy_data():
    record = {
        "device_key": "fw-prod-01",
        "rule_name": "allow-api",
        "policy_data": {
            "source_port": "49152",
            "destination_port": "8443",
            "service": "internal-api",
        },
    }

    assert manager.json_matches_keyword(record, "8443")
    assert manager.json_matches_keyword(record, "INTERNAL")
    assert not manager.json_matches_keyword(record, "database")


def test_policy_history_keyword_search_matches_before_after_snapshots():
    record = {
        "policy_name": "allow-office",
        "before_data": {"target_address": "10.10.10.10"},
        "after_data": {"target_address": "10.10.20.20", "service": "ssh"},
    }

    assert manager.json_matches_keyword(record, "10.10.20.20")
    assert manager.json_matches_keyword(record, "SSH")
    assert not manager.json_matches_keyword(record, "rdp")


def test_access_control_process_configs_enable_full_field_keyword_search():
    assert manager.process_config("actual-policy")["keyword_all_fields"] is True
    assert manager.process_config("policy-change")["keyword_all_fields"] is True
    assert manager.process_config("preset-policy").get("keyword_all_fields") is None
