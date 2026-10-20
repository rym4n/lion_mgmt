from copy import deepcopy

RULE_TEXT = (
    "rule name allow-web\n source-zone trust\n destination-zone untrust\n"
    " destination-address 10.0.0.5 32\n service http\n action permit"
)
RAW_OUTPUTS = {
    "r1": [{"item_id": "i1", "app_name": "huawei_usg", "action_func": "collect_policy_observations",
            "app_version": "1.0", "output": {"outputs": [{"source": "display security-policy all", "content": RULE_TEXT}], "errors": []}}],
    "r2": [{"item_id": "i2", "app_name": "fortigate", "action_func": "collect_policy_observations",
            "app_version": "2.0", "output": {"outputs": [{"source": "/api/v2/cmdb/firewall/policy",
            "content": {"results": [{"policyid": 7, "name": "dns", "action": "accept"}]}}], "errors": []}}],
}
TARGETS = [{"resource_id": "r1", "resource_name": "FW-1", "app_name": "huawei_usg"},
           {"resource_id": "r2", "resource_name": "FW-2", "app_name": "fortigate"}]


def policy(**overrides):
    value = {"policy_key": "allow-web", "rule_name": "allow-web", "source_zone": "trust",
             "target_zone": "untrust", "target_address": "10.0.0.5/32", "destination_port": "80",
             "protocol": "tcp", "service": "http", "action": "permit", "enabled": True,
             "policy_order": 0, "parse_status": "parsed", "raw_text": RULE_TEXT}
    value.update(overrides)
    return value


def document(resource_id="r1", policies=None):
    return {"resource_id": resource_id, "device_type": "firewall", "policy_type": "firewall",
            "policies": deepcopy(policies) if policies is not None else [policy()]}


def observations(resource_id="r1"):
    from services.network_access_control.collection.agent_jobs import observations_for
    return observations_for(RAW_OUTPUTS, resource_id)
