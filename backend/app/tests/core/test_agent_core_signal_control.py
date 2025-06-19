import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call
from uuid import UUID, uuid4

import pytest

from app.core.agent_core import AgentCore, GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS, ACTION_KPI_CONFIG
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.tasks.prediction_scheduler import PredictionScheduler
from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalOperationalStatusEnum,
    SignalControlCommandResponse, SignalControlStatusEnum
)
from app.models.traffic import LocationModel

# Helper to create a default valid candidate signal state
def create_candidate_signal(signal_id: str, current_phase: SignalPhaseEnum = SignalPhaseEnum.RED) -> SignalState:
    return SignalState(
        signal_id=signal_id, current_phase=current_phase,
        operational_status=SignalOperationalStatusEnum.ONLINE,
        location=LocationModel(latitude=1.0, longitude=1.0, name=signal_id), # Basic location
        last_updated=datetime.utcnow(), # Needs to be fresh for no cooldown issues if not mocked
        main_flow_direction="NS" # Example
    )

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    default_kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: default_kpis[kpi_name] = "LOW"
    service.get_current_system_kpis_summary = MagicMock(return_value=default_kpis)
    service.get_critical_alert_summary = AsyncMock(return_value={"critical_unack_alert_count": 0, "recent_critical_types": [], "active_alerts": []})
    service.get_signal_post_action_kpis = AsyncMock(return_value={"flow_rate_absolute": 100})
    service.get_incident_response_post_action_kpis = AsyncMock(return_value={"clearance_time_seconds": 500})
    service.get_corridor_post_action_kpis = AsyncMock(return_value={"corridor_avg_travel_time_seconds": 90})
    service.broadcast_operational_alert = AsyncMock(); service.send_user_specific_alert = AsyncMock()
    service.predict_incident_likelihood = AsyncMock(return_value={"likelihood_score_percent": 0})
    return service

@pytest.fixture
def mock_prediction_scheduler():
    service = MagicMock(spec=PredictionScheduler); service.set_priority_locations = AsyncMock(); return service

@pytest.fixture
def mock_personalized_routing_service():
    service = MagicMock(spec=PersonalizedRoutingService); service.proactively_suggest_route = AsyncMock(return_value=None)
    service.get_user_common_travel_patterns = AsyncMock(return_value=[]); return service

@pytest.fixture
def mock_traffic_signal_service():
    service = MagicMock(spec=TrafficSignalService); service.get_all_signal_states = AsyncMock(return_value=[])
    service.set_signal_phase = AsyncMock(return_value=SignalControlCommandResponse(signal_id="test_signal_id", status=SignalControlStatusEnum.ACCEPTED,message="Command accepted.",timestamp=datetime.utcnow()))
    return service

@pytest.fixture
def agent_core_with_patched_logger(mock_prediction_scheduler, mock_personalized_routing_service, mock_analytics_service, mock_traffic_signal_service):
    with patch('app.core.agent_core.logger', MagicMock(spec=logging.Logger)) as mock_logger:
        core = AgentCore(mock_prediction_scheduler, mock_personalized_routing_service, mock_analytics_service, mock_traffic_signal_service)
        core.logger = mock_logger; core.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
        core.action_performance_logs = []; core.pending_kpi_collection = []; core.action_effectiveness_memory = {}
        return core

# --- Existing Test Cases ... (Assumed present) ...
# For brevity, only new tests for adaptive congestion logic are shown below.

# --- New Test Cases for Adaptive Congestion Logic ---

@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_congestion_logic_selects_highest_score_signal(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_A = create_candidate_signal("sig_A", current_phase=SignalPhaseEnum.RED)
    sig_B = create_candidate_signal("sig_B", current_phase=SignalPhaseEnum.RED) # Highest score
    sig_C = create_candidate_signal("sig_C", current_phase=SignalPhaseEnum.RED)
    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_A, sig_B, sig_C]

    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_A"] = [0.1, 0.3] # Avg 0.2
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_B"] = [0.7, 0.9] # Avg 0.8
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_C"] = [-0.4, -0.6] # Avg -0.5

    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(signal_id="sig_B", status=SignalControlStatusEnum.ACCEPTED, timestamp=now)

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_B", phase=SignalPhaseEnum.GREEN, duration_seconds=60)
    agent_core.logger.info.assert_any_call("Selected signal 'sig_B' for congestion relief with avg score: 0.80. Candidates: [{'id': 'sig_B', 'score': 0.8}, {'id': 'sig_A', 'score': 0.2}, {'id': 'sig_C', 'score': -0.5}]")

@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_congestion_logic_selects_first_candidate_if_no_scores(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_X = create_candidate_signal("sig_X")
    sig_Y = create_candidate_signal("sig_Y")
    # Order matters for default selection if scores are tied (or 0.0)
    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_X, sig_Y]
    agent_core.action_effectiveness_memory = {} # No history

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_X", phase=SignalPhaseEnum.GREEN, duration_seconds=60)
    agent_core.logger.info.assert_any_call(f"Selected signal 'sig_X' for congestion relief with avg score: 0.00. Candidates: [{'id': 'sig_X', 'score': 0.0}, {{'id': 'sig_Y', 'score': 0.0}}]")

@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_congestion_logic_selects_first_candidate_on_tied_scores(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_P = create_candidate_signal("sig_P") # Iterated first
    sig_Q = create_candidate_signal("sig_Q")
    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_P, sig_Q]
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_P"] = [0.5]
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_Q"] = [0.5]

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_P", phase=SignalPhaseEnum.GREEN, duration_seconds=60)
    agent_core.logger.info.assert_any_call(f"Selected signal 'sig_P' for congestion relief with avg score: 0.50. Candidates: [{'id': 'sig_P', 'score': 0.5}, {{'id': 'sig_Q', 'score': 0.5}}]")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_congestion_logic_selects_sole_candidate(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sole_sig = create_candidate_signal("sig_sole")
    mock_traffic_signal_service.get_all_signal_states.return_value = [sole_sig]
    # Memory can be empty or have any score, it's the only choice
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_sole"] = [0.3]


    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_sole", phase=SignalPhaseEnum.GREEN, duration_seconds=60)
    agent_core.logger.info.assert_any_call(f"Selected signal 'sig_sole' for congestion relief with avg score: 0.30. Candidates: [{'id': 'sig_sole', 'score': 0.3}]")

@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_congestion_logic_skips_all_if_not_suitable(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    # All signals are green, or offline, or on cooldown
    sig_green = create_candidate_signal("sig_green_already", current_phase=SignalPhaseEnum.GREEN)
    sig_offline = create_candidate_signal("sig_off"); sig_offline.operational_status = SignalOperationalStatusEnum.OFFLINE
    sig_cooldown = create_candidate_signal("sig_cool");
    agent_core._recent_signal_actions["sig_cool"] = {'timestamp': now - timedelta(seconds=30), 'reason': 'any'}

    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_green, sig_offline, sig_cooldown]

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("High congestion: No suitable signals found for general congestion relief after filtering.")

# (Ensure other test categories like incident response, green wave scheduling, KPI processing are also present)
# ...
# test_schedule_kpi_for_set_signal_green_congestion needs to be aware that now only one signal (the best one) is chosen.
# The KPI scheduling should reflect the signal that was actually chosen by the adaptive logic.
@patch('app.core.agent_core.uuid4')
@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_schedule_kpi_for_adaptively_chosen_signal_green_congestion(mock_dt, mock_uuid, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    test_uuid = UUID('abcdef12-1234-5678-1234-567812345678'); mock_uuid.return_value = test_uuid

    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_A = create_candidate_signal("sig_A_kpi_test", current_phase=SignalPhaseEnum.RED) # Lower score
    sig_B = create_candidate_signal("sig_B_kpi_test", current_phase=SignalPhaseEnum.RED) # Higher score, should be chosen

    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_A, sig_B]
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_A_kpi_test"] = [0.1]
    agent_core.action_effectiveness_memory["SET_SIGNAL_GREEN_CONGESTION:sig_B_kpi_test"] = [0.9] # sig_B will be chosen

    # Ensure set_signal_phase mock returns success for the chosen signal
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration_seconds: \
        asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=now)) if signal_id == "sig_B_kpi_test" \
        else asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.FAILED, timestamp=now))


    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_B_kpi_test", phase=SignalPhaseEnum.GREEN, duration_seconds=60)

    assert len(agent_core.pending_kpi_collection) == 1
    pending_item = agent_core.pending_kpi_collection[0]
    action_type = "SET_SIGNAL_GREEN_CONGESTION"
    kpi_cfg = ACTION_KPI_CONFIG[action_type]

    assert pending_item['action_id'] == test_uuid
    assert pending_item['action_type'] == action_type
    assert pending_item['target_ids'] == ["sig_B_kpi_test"] # Ensure it's for the chosen signal
    assert pending_item['action_parameters'] == {"phase": SignalPhaseEnum.GREEN.value, "duration_seconds": 60}
    # Pre-action context should reflect the state of sig_B
    assert pending_item['pre_action_context_kpis'] == {"overall_congestion": "HIGH", "signal_initial_phase": SignalPhaseEnum.RED.value}
    assert pending_item['query_after_timestamp'] == now + timedelta(seconds=kpi_cfg["delay_seconds"])
