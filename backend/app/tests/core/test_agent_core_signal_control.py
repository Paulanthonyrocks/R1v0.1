import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call
from uuid import UUID, uuid4

import pytest

from app.core.agent_core import AgentCore, GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS, ACTION_KPI_CONFIG # Import configs
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.tasks.prediction_scheduler import PredictionScheduler
from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalOperationalStatusEnum,
    SignalControlCommandResponse, SignalControlStatusEnum
)
from app.models.traffic import LocationModel
from app.models.signals import LocationModel # Ensure LocationModel is available if used by AnalyticsService mocks

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    default_kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: default_kpis[kpi_name] = "LOW"
    service.get_current_system_kpis_summary = MagicMock(return_value=default_kpis)
    service.get_critical_alert_summary = AsyncMock(return_value={"critical_unack_alert_count": 0, "recent_critical_types": [], "active_alerts": []})

    # Mock KPI collection methods
    service.get_signal_post_action_kpis = AsyncMock(return_value={"flow_rate_absolute": 100})
    service.get_incident_response_post_action_kpis = AsyncMock(return_value={"clearance_time_seconds": 500}) # Aligned name
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
        # Reset lists for each test
        core.action_performance_logs = []
        core.pending_kpi_collection = []
        return core

# --- Existing Test Cases ... (Assumed present) ...

# --- New Test Cases for KPI Collection Scheduling ---

@patch('app.core.agent_core.uuid4')
@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_schedule_kpi_for_set_signal_green_congestion(mock_dt, mock_uuid, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    mock_uuid.return_value = UUID('12345678-1234-5678-1234-567812345678')

    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    sig_id = "TS001_congestion_test"
    initial_phase = SignalPhaseEnum.RED
    mock_signal_states = [SignalState(signal_id=sig_id, current_phase=initial_phase, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1,longitude=1), last_updated=now)]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(signal_id=sig_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=now)

    await agent_core.run_decision_cycle()

    assert len(agent_core.pending_kpi_collection) == 1
    pending_item = agent_core.pending_kpi_collection[0]
    action_type = "SET_SIGNAL_GREEN_CONGESTION"
    kpi_cfg = ACTION_KPI_CONFIG[action_type]

    assert pending_item['action_id'] == UUID('12345678-1234-5678-1234-567812345678')
    assert pending_item['action_type'] == action_type
    assert pending_item['target_ids'] == [sig_id]
    assert pending_item['action_parameters'] == {"phase": SignalPhaseEnum.GREEN.value, "duration_seconds": 60}
    assert pending_item['pre_action_context_kpis'] == {"overall_congestion": "HIGH", "signal_initial_phase": initial_phase.value}
    assert pending_item['query_after_timestamp'] == now + timedelta(seconds=kpi_cfg["delay_seconds"])
    assert pending_item['metrics_to_collect'] == kpi_cfg["metrics"]
    assert pending_item['kpi_query_details']['service_method_name'] == kpi_cfg["service_method"]
    assert pending_item['kpi_query_details']['method_specific_args'] == {'signal_id': sig_id}


@patch('app.core.agent_core.uuid4')
@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_schedule_kpi_for_green_wave_activation(mock_dt, mock_uuid, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,8,0,0); mock_dt.utcnow.return_value = now # Time for main_st_ns_wave
    mock_uuid.return_value = UUID('abcdef01-1234-5678-1234-567812345678')
    corridor_id = "main_st_ns_wave"
    config = GREEN_WAVE_CORRIDOR_CONFIGS[corridor_id]

    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    kpis[config["demand_kpi_trigger"]] = "HIGH" # Trigger by demand for simplicity here
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis

    mock_signal_states = {sid: SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1,longitude=1)) for sid in config["signals_in_order"]}
    mock_traffic_signal_service.get_all_signal_states.return_value = list(mock_signal_states.values())
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration: asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=now))


    await agent_core.run_decision_cycle()

    # Assuming _execute_green_wave successfully adds one item to pending_kpi_collection
    # if it processes the whole wave as one "action" for KPI purposes.
    # The current implementation in previous step adds one entry after wave execution attempt.
    assert len(agent_core.pending_kpi_collection) >= 1
    pending_item = next((item for item in agent_core.pending_kpi_collection if item['action_type'] == "GREEN_WAVE_ACTIVATION"), None)
    assert pending_item is not None

    action_type = "GREEN_WAVE_ACTIVATION"
    kpi_cfg = ACTION_KPI_CONFIG[action_type]
    assert pending_item['action_id'] == UUID('abcdef01-1234-5678-1234-567812345678') # if uuid4 is patched for the entry
    assert pending_item['target_ids'] == [corridor_id] + config["signals_in_order"]
    assert pending_item['kpi_query_details']['service_method_name'] == kpi_cfg["service_method"]
    assert pending_item['kpi_query_details']['method_specific_args'] == {'corridor_id': corridor_id}


# --- New Test Cases for Processing Pending KPI Collection ---

@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_process_pending_kpi_collection_calls_correct_method(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,10,0) # Current time is past query_after_timestamp
    mock_dt.utcnow.return_value = now

    action_id_test = uuid4()
    kpi_cfg_signal = ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]
    pending_item = {
        'action_id': action_id_test, 'action_type': "SET_SIGNAL_GREEN_CONGESTION",
        'target_ids': ["TS001"], 'action_timestamp': now - timedelta(seconds=kpi_cfg_signal["delay_seconds"] + 60), # Ensure it's due
        'action_parameters': {"phase": "GREEN"}, 'pre_action_context_kpis': {"congestion": "HIGH"},
        'query_after_timestamp': now - timedelta(seconds=30), # Due for collection
        'metrics_to_collect': kpi_cfg_signal["metrics"],
        'evaluation_window_minutes': kpi_cfg_signal["eval_window_minutes"],
        'kpi_query_details': {'service_method_name': kpi_cfg_signal["service_method"],
                              'method_specific_args': {'signal_id': "TS001"}}
    }
    agent_core.pending_kpi_collection.append(pending_item)
    mock_analytics_service.get_signal_post_action_kpis.return_value = {"flow_rate_absolute": 200}

    await agent_core.run_decision_cycle()

    mock_analytics_service.get_signal_post_action_kpis.assert_called_once()
    called_args = mock_analytics_service.get_signal_post_action_kpis.call_args[1] # kwargs
    assert called_args['signal_id'] == "TS001"
    assert called_args['action_type'] == "SET_SIGNAL_GREEN_CONGESTION"
    assert called_args['metrics_to_collect'] == kpi_cfg_signal["metrics"]

    assert len(agent_core.action_performance_logs) == 1
    log_entry = agent_core.action_performance_logs[0]
    assert log_entry.action_id == action_id_test
    assert log_entry.post_action_kpis == {"flow_rate_absolute": 200}
    assert log_entry.kpi_collection_timestamp == now
    assert len(agent_core.pending_kpi_collection) == 0


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_process_pending_kpi_collection_item_not_due(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now

    pending_item = { # query_after_timestamp is in the future
        'action_id': uuid4(), 'action_type': "TEST_ACTION", 'target_ids': ["T1"],
        'action_timestamp': now - timedelta(seconds=60), 'action_parameters': {}, 'pre_action_context_kpis': {},
        'query_after_timestamp': now + timedelta(minutes=5), # Not due
        'metrics_to_collect': ["m1"], 'evaluation_window_minutes': 5,
        'kpi_query_details': {'service_method_name': "get_signal_post_action_kpis", 'method_specific_args': {'signal_id': "T1"}}
    }
    agent_core.pending_kpi_collection.append(pending_item)

    await agent_core.run_decision_cycle()

    mock_analytics_service.get_signal_post_action_kpis.assert_not_called()
    assert len(agent_core.pending_kpi_collection) == 1
    assert len(agent_core.action_performance_logs) == 0

@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_process_pending_kpi_collection_analytics_service_call_fails(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    action_id_test = uuid4()
    pending_item = {
        'action_id': action_id_test, 'action_type': "FAIL_ACTION", 'target_ids': ["TF1"],
        'action_timestamp': now - timedelta(minutes=10), 'action_parameters': {}, 'pre_action_context_kpis': {},
        'query_after_timestamp': now - timedelta(minutes=1), # Due
        'metrics_to_collect': ["m1"], 'evaluation_window_minutes': 5,
        'kpi_query_details': {'service_method_name': "get_signal_post_action_kpis", 'method_specific_args': {'signal_id': "TF1"}}
    }
    agent_core.pending_kpi_collection.append(pending_item)
    mock_analytics_service.get_signal_post_action_kpis.side_effect = Exception("Analytics Service Down")

    await agent_core.run_decision_cycle()

    mock_analytics_service.get_signal_post_action_kpis.assert_called_once()
    agent_core.logger.error.assert_any_call(f"Error collecting KPIs for action {action_id_test} via get_signal_post_action_kpis: Analytics Service Down", exc_info=True)
    assert len(agent_core.action_performance_logs) == 1
    assert agent_core.action_performance_logs[0].action_id == action_id_test
    assert agent_core.action_performance_logs[0].post_action_kpis is None # Failure results in None
    assert len(agent_core.pending_kpi_collection) == 0


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_process_pending_kpi_collection_analytics_method_missing(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0); mock_dt.utcnow.return_value = now
    action_id_test = uuid4()
    pending_item = {
        'action_id': action_id_test, 'action_type': "MISSING_METHOD_ACTION", 'target_ids': ["TM1"],
        'action_timestamp': now - timedelta(minutes=10), 'action_parameters': {}, 'pre_action_context_kpis': {},
        'query_after_timestamp': now - timedelta(minutes=1), # Due
        'metrics_to_collect': ["m1"], 'evaluation_window_minutes': 5,
        'kpi_query_details': {'service_method_name': "non_existent_method", 'method_specific_args': {'signal_id': "TM1"}}
    }
    agent_core.pending_kpi_collection.append(pending_item)

    await agent_core.run_decision_cycle()
    agent_core.logger.error.assert_any_call(f"AnalyticsService method 'non_existent_method' not found for action {action_id_test}.")
    assert len(agent_core.action_performance_logs) == 1
    assert agent_core.action_performance_logs[0].post_action_kpis is None
    assert len(agent_core.pending_kpi_collection) == 0

# Placeholder for other tests from previous steps
# test_road_closure_nearby_green_signal_set_to_red, etc.
# test_green_wave_execution_sequence_and_timing etc.
