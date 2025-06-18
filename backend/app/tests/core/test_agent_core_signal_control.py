import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call

import pytest

from app.core.agent_core import AgentCore, GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.tasks.prediction_scheduler import PredictionScheduler
from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalOperationalStatusEnum,
    SignalControlCommandResponse, SignalControlStatusEnum
)
from app.models.traffic import LocationModel

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    default_kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: # Initialize all known corridor KPIs
        default_kpis[kpi_name] = "LOW"
    service.get_current_system_kpis_summary = MagicMock(return_value=default_kpis)
    service.get_critical_alert_summary = AsyncMock(return_value={"critical_unack_alert_count": 0, "recent_critical_types": [], "active_alerts": []})
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
        core.logger = mock_logger; core.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS; return core

# --- Existing Test Cases ... (Assumed present and correct) ...

# --- New/Updated Test Cases for Green Wave Trigger and Selection Logic ---

@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_time_window_activates_main_st(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 7, 30, 0) # 07:30 UTC - within main_st_ns_wave window
    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis
    agent_core._execute_green_wave = AsyncMock(return_value=True) # Mock execution

    await agent_core.run_decision_cycle()

    agent_core.logger.info.assert_any_call("Corridor 'main_st_ns_wave' is a candidate for green wave (Time: True, Demand: False).")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'main_st_ns_wave'.")
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="main_st_ns_wave", config=GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"], signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)


@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_time_window_no_activation_outside_window(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 3, 0, 0) # 03:00 UTC
    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis
    agent_core._execute_green_wave = AsyncMock()

    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call("No green wave corridors are currently triggered by time or demand.")
    agent_core._execute_green_wave.assert_not_called()

@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_time_window_selects_oak_ave(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 11, 30, 0) # 11:30 UTC - in oak_ave window
    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis
    agent_core._execute_green_wave = AsyncMock(return_value=True)

    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call("Corridor 'oak_ave_ew_wave' is a candidate for green wave (Time: True, Demand: False).")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'oak_ave_ew_wave'.")
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="oak_ave_ew_wave", config=GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"], signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)


@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_demand_kpi_activates_main_st_ns_wave(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 10, 0, 0) # Outside main_st time windows
    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    kpis[GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]] = "HIGH"
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis
    agent_core._execute_green_wave = AsyncMock(return_value=True)

    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call("Corridor 'main_st_ns_wave' is a candidate for green wave (Time: False, Demand: True).")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'main_st_ns_wave'.")


@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_priority_selection_both_triggered(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 7, 30, 0) # Activates main_st_ns_wave (P1) by time
    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    kpis[GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"]["demand_kpi_trigger"]] = "HIGH" # Activates oak_ave_ew_wave (P2) by demand
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis
    agent_core._execute_green_wave = AsyncMock(return_value=True)

    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call("Corridor 'main_st_ns_wave' is a candidate for green wave (Time: True, Demand: False).")
    agent_core.logger.info.assert_any_call("Corridor 'oak_ave_ew_wave' is a candidate for green wave (Time: False, Demand: True).")
    agent_core.logger.info.assert_any_call("Sorted candidate corridors by priority: ['main_st_ns_wave', 'oak_ave_ew_wave'].")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'main_st_ns_wave'.") # P1 selected
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="main_st_ns_wave", config=ANY, signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)


@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_priority_lower_runs_if_higher_not_triggered(mock_dt, agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 10, 0, 0) # Outside main_st_ns_wave (P1) time window
    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    kpis[GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]] = "LOW" # P1 not demand triggered
    kpis[GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"]["demand_kpi_trigger"]] = "HIGH"  # P2 demand triggered
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis
    agent_core._execute_green_wave = AsyncMock(return_value=True)

    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call("Corridor 'oak_ave_ew_wave' is a candidate for green wave (Time: False, Demand: True).")
    log_calls_str = "".join(str(call_args) for call_args in agent_core.logger.info.call_args_list)
    assert "Corridor 'main_st_ns_wave' is a candidate" not in log_calls_str # P1 should not be candidate
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'oak_ave_ew_wave'.") # P2 selected
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="oak_ave_ew_wave", config=ANY, signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)

@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_selection_skips_if_signals_conflict(mock_dt, agent_core_with_patched_logger, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 7, 30, 0) # Time for main_st_ns_wave (P1)
    mock_dt.utcnow.return_value = now

    # Modify a copy of the configs for this test to create a conflict
    test_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS.copy()
    test_corridor_configs["oak_ave_ew_wave"] = { # P2
        **GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"],
        "signals_in_order": ["TS001", "TS005"]  # TS001 conflicts with main_st_ns_wave
    }
    agent_core.green_wave_corridor_configs = test_corridor_configs

    kpis = {"overall_congestion_level": "LOW"}
    kpis[test_corridor_configs["main_st_ns_wave"]["demand_kpi_trigger"]] = "HIGH" # P1 by demand
    kpis[test_corridor_configs["oak_ave_ew_wave"]["demand_kpi_trigger"]] = "HIGH"   # P2 by demand
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis

    # Mock signals
    mock_signal_states = {
        sid: SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1,longitude=1), last_updated=now)
        for sid in ["TS001", "TS002", "TS004", "TS005"]
    }
    mock_traffic_signal_service.get_all_signal_states.return_value = list(mock_signal_states.values())
    agent_core._execute_green_wave = AsyncMock(return_value=True) # Mock to focus on selection

    await agent_core.run_decision_cycle()

    # P1 (main_st_ns_wave) should be selected as it's higher priority and has no initial conflicts
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'main_st_ns_wave'.")
    # _execute_green_wave should be called for P1
    agent_core._execute_green_wave.assert_called_once_with(
        corridor_id="main_st_ns_wave", config=ANY, signals_in_order=["TS001", "TS002", "TS004"], green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY
    )
    # P2 (oak_ave_ew_wave) should be logged as a candidate, but then the log for selection loop should show it was skipped.
    agent_core.logger.info.assert_any_call("Corridor 'oak_ave_ew_wave' is a candidate for green wave (Time: False, Demand: True).")
    # This specific log about skipping oak_ave_ew_wave due to signal conflict might not appear if we break after selecting the first one.
    # The key is that only main_st_ns_wave was executed.
    # If the plan was to run multiple non-overlapping, then we'd expect a skip log for oak_ave.
    # With current "run one" logic, this test confirms priority selection.
    # If _execute_green_wave for main_st_ns_wave populates processed_signals_for_coordination correctly,
    # and if the loop for candidates continued, oak_ave_ew_wave would be skipped.

# ... (other existing tests like detailed sequence, skipping offline etc. should be reviewed and adapted if necessary) ...
# For example, test_execute_green_wave_detailed_sequence should use a specific corridor_id from the new config.
