from models._network_topology_v2_base import AnalysisRunStage
from models._network_topology_v2_analysis import TopologyAnalysisRun
from services.network_topology import leases


def test_agent_stage_sits_between_parsing_and_validating_facts():
    order = list(leases.STAGE_ORDER)
    parsing = order.index(AnalysisRunStage.PARSING)
    assert order[parsing + 1] == AnalysisRunStage.AGENT_NORMALIZING
    assert order[parsing + 2] == AnalysisRunStage.VALIDATING_FACTS
    assert leases.next_stage(AnalysisRunStage.PARSING) == AnalysisRunStage.AGENT_NORMALIZING


def test_run_has_graph_and_agent_fields():
    fields = TopologyAnalysisRun._meta.fields_map
    assert fields["graph_payload"].null is True
    assert fields["agent_observation_ids"].null is True
