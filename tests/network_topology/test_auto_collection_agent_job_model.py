from models.network_topology_v2 import TopologyAgentJob


def test_agent_job_table_and_defaults():
    assert TopologyAgentJob._meta.db_table == "network_topology_agent_job"
    fields = TopologyAgentJob._meta.fields_map
    assert fields["state"].default == "QUEUED"
    assert fields["documents"].null is True
    assert fields["transcript"].null is True
