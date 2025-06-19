import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call, mock_open
import json
import os
from uuid import UUID, uuid4 # Added for test_calculate_effectiveness_score_green_wave

import pytest

from app.core.agent_core import AgentCore, GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS, ACTION_KPI_CONFIG, ACTION_EFFECTIVENESS_CONFIG # Import configs
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.tasks.prediction_scheduler import PredictionScheduler
from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalOperationalStatusEnum,
    SignalControlCommandResponse, SignalControlStatusEnum
)
from app.models.traffic import LocationModel

# Helper to create a default valid candidate signal state (if not already present from previous steps)
def create_candidate_signal(signal_id: str, current_phase: SignalPhaseEnum = SignalPhaseEnum.RED) -> SignalState:
    return SignalState(
        signal_id=signal_id, current_phase=current_phase,
        operational_status=SignalOperationalStatusEnum.ONLINE,
        location=LocationModel(latitude=1.0, longitude=1.0, name=signal_id),
        last_updated=datetime.utcnow(), main_flow_direction="NS"
    )

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    default_kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: default_kpis[kpi_name] = "LOW"
    service.get_current_system_kpis_summary = MagicMock(return_value=default_kpis)
    service.get_critical_alert_summary = AsyncMock(return_value={"critical_unack_alert_count": 0, "recent_critical_types": [], "active_alerts": []})

    # Updated default for get_corridor_post_action_kpis
    service.get_corridor_post_action_kpis = AsyncMock(return_value={
        "corridor_avg_travel_time_seconds": 110,
        "corridor_throughput_vph": 750, # Changed from corridor_throughput_vehicle_per_hour
        "queried_corridor_id": "default_corridor_test"
    })
    service.get_signal_post_action_kpis = AsyncMock(return_value={"flow_rate_absolute": 100, "local_congestion_level": "LOW"})
    service.get_incident_response_post_action_kpis = AsyncMock(return_value={"clearance_time_seconds": 500, "local_congestion_level_incident_zone": "LOW"})

    service.broadcast_operational_alert = AsyncMock(); service.send_user_specific_alert = AsyncMock()
    service.predict_incident_likelihood = AsyncMock(return_value={"likelihood_score_percent": 0})
    return service

@pytest.fixture
def agent_core_with_patched_logger_and_persistence(mock_prediction_scheduler, mock_personalized_routing_service, mock_analytics_service, mock_traffic_signal_service):
    with patch('app.core.agent_core.logger', MagicMock(spec=logging.Logger)) as mock_logger:
        with patch('app.core.agent_core.AgentCore._load_effectiveness_memory', return_value={}) as mock_load_mem:
            core = AgentCore(mock_prediction_scheduler, mock_personalized_routing_service, mock_analytics_service, mock_traffic_signal_service)
            core.logger = mock_logger
            core.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
            core.action_effectiveness_config = ACTION_EFFECTIVENESS_CONFIG # Ensure this is also set
            core.action_performance_logs = []
            core.pending_kpi_collection = []
            core.action_effectiveness_memory = {}
            core.effectiveness_memory_filepath = "test_data/test_memory.json"
            test_data_dir = os.path.dirname(core.effectiveness_memory_filepath)
            if not os.path.exists(test_data_dir): os.makedirs(test_data_dir, exist_ok=True)
            return core

# --- Existing Test Cases ... (Assumed present) ...

# --- Test Cases for _calculate_effectiveness_score (Green Wave Specific) ---

def test_calculate_effectiveness_score_green_wave_good_performance(agent_core_with_patched_logger_and_persistence):
    agent_core = agent_core_with_patched_logger_and_persistence
    log_entry_data = {
        "action_id": uuid4(), "action_type": "GREEN_WAVE_ACTIVATION", "target_ids": ["main_st_ns_wave"],
        "action_timestamp": datetime.utcnow(), "action_parameters": {},
        "pre_action_context_kpis": {"corridor_id": "main_st_ns_wave", "expected_demand_level": "HIGH"},
        "post_action_kpis": {"corridor_avg_travel_time_seconds": 70, "corridor_throughput_vph": 900} # Good values
    }
    score, metrics_used = agent_core._calculate_effectiveness_score(log_entry_data)
    assert score is not None
    assert score > 0.5 # Expecting a high positive score
    assert metrics_used == {
        "gw_post_avg_travel_time": 70, "gw_post_throughput": 900,
        "gw_corridor_id": "main_st_ns_wave", "gw_pre_demand_level": "HIGH"
    }

def test_calculate_effectiveness_score_green_wave_poor_performance(agent_core_with_patched_logger_and_persistence):
    agent_core = agent_core_with_patched_logger_and_persistence
    log_entry_data = {
        "action_id": uuid4(), "action_type": "GREEN_WAVE_ACTIVATION", "target_ids": ["main_st_ns_wave"],
        "action_timestamp": datetime.utcnow(), "action_parameters": {},
        "pre_action_context_kpis": {"corridor_id": "main_st_ns_wave", "expected_demand_level": "HIGH"},
        "post_action_kpis": {"corridor_avg_travel_time_seconds": 200, "corridor_throughput_vph": 300} # Poor values
    }
    score, metrics_used = agent_core._calculate_effectiveness_score(log_entry_data)
    assert score is not None
    assert score < 0.0 # Expecting a negative score
    assert metrics_used == {
        "gw_post_avg_travel_time": 200, "gw_post_throughput": 300,
        "gw_corridor_id": "main_st_ns_wave", "gw_pre_demand_level": "HIGH"
    }

def test_calculate_effectiveness_score_green_wave_missing_kpis(agent_core_with_patched_logger_and_persistence):
    agent_core = agent_core_with_patched_logger_and_persistence
    log_entry_data = {
        "action_id": uuid4(), "action_type": "GREEN_WAVE_ACTIVATION", "target_ids": ["main_st_ns_wave"],
        "action_timestamp": datetime.utcnow(), "action_parameters": {},
        "pre_action_context_kpis": {"corridor_id": "main_st_ns_wave"}, # Missing demand level
        "post_action_kpis": {} # Missing both travel time and throughput
    }
    score, metrics_used = agent_core._calculate_effectiveness_score(log_entry_data)
    assert score is None # Should be None as per refined scoring logic if no relevant KPIs
    assert metrics_used == {"gw_corridor_id": "main_st_ns_wave"} # Still extracts what it can
    agent_core.logger.warning.assert_any_call(
        "Green wave efficiency scoring for corridor 'main_st_ns_wave': No relevant post-action KPIs (travel time or throughput) found in metrics: {'gw_corridor_id': 'main_st_ns_wave'}"
    )


# --- Review and Confirm Existing Adaptive Green Wave Selection Tests ---
# These tests rely on action_effectiveness_memory being populated correctly before run_decision_cycle.
# The avg_score is now calculated *inside* run_decision_cycle when candidates are identified.

@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_priority_selection_uses_avg_score_tie_breaker(mock_dt, agent_core_with_patched_logger_and_persistence, mock_analytics_service, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    now = datetime(2023, 1, 1, 7, 30, 0) # Time for main_st_ns_wave (P1) & oak_ave_ew_wave (P2 based on its time_window if it was 07:30)
    mock_dt.utcnow.return_value = now

    # Scenario: main_st (P1) and oak_ave (P2) are both candidates.
    # main_st has lower priority number (higher actual priority).
    # We'll give oak_ave a better score to see if priority still wins.
    # Then, we'll make them same priority and see if score wins.

    # Configs: main_st_ns_wave (P1), oak_ave_ew_wave (P2)
    main_st_cfg = GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]
    oak_ave_cfg = GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"]

    # Both triggered by time
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 7, 30, 0) # main_st active
    # To make oak_ave also time-active for this test, let's temporarily adjust its window for the test, or assume a test config
    # For simplicity, let's assume both are time-triggered by setting current time within both windows if possible,
    # or rely on demand triggers. Let's use demand for oak_ave.

    kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: kpis[kpi_name] = "LOW"
    # main_st_ns_wave (P1) is time-triggered (07:30).
    kpis[oak_ave_cfg["demand_kpi_trigger"]] = "HIGH" # oak_ave_ew_wave (P2) demand-triggered.
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis

    agent_core.action_effectiveness_memory[f"GREEN_WAVE_ACTIVATION:main_st_ns_wave"] = [-0.5] # Lower score for P1 wave
    agent_core.action_effectiveness_memory[f"GREEN_WAVE_ACTIVATION:oak_ave_ew_wave"] = [0.8]  # Higher score for P2 wave

    # Mock signals for both corridors
    all_sids = main_st_cfg["signals_in_order"] + oak_ave_cfg["signals_in_order"]
    mock_signal_states = [create_candidate_signal(sid) for sid in all_sids]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states
    agent_core._execute_green_wave = AsyncMock(return_value=True)

    await agent_core.run_decision_cycle()

    # Assert main_st_ns_wave (P1) is chosen due to higher priority, despite lower score
    agent_core.logger.info.assert_any_call("Sorted candidate corridors by priority: ['main_st_ns_wave' (Prio: 1, AvgScore: -0.50), 'oak_ave_ew_wave' (Prio: 2, AvgScore: 0.80)].")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'main_st_ns_wave'.")
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="main_st_ns_wave", config=main_st_cfg, signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)

    # Now, make them same priority and check if score tie-breaks
    agent_core._execute_green_wave.reset_mock()
    agent_core.logger.reset_mock()
    agent_core.action_effectiveness_memory.clear() # Clear previous memory for this part

    # Create a temporary config where priorities are the same
    agent_core.green_wave_corridor_configs["main_st_ns_wave"]["priority"] = 1
    agent_core.green_wave_corridor_configs["oak_ave_ew_wave"]["priority"] = 1 # Same priority
    agent_core.action_effectiveness_memory[f"GREEN_WAVE_ACTIVATION:main_st_ns_wave"] = [-0.5] # Lower score for main_st
    agent_core.action_effectiveness_memory[f"GREEN_WAVE_ACTIVATION:oak_ave_ew_wave"] = [0.8]  # Higher score for oak_ave

    # Both triggered by demand, time outside windows
    mock_dt.utcnow.return_value = datetime(2023, 1, 1, 10, 0, 0)
    kpis[main_st_cfg["demand_kpi_trigger"]] = "HIGH"
    kpis[oak_ave_cfg["demand_kpi_trigger"]] = "HIGH"
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis

    await agent_core.run_decision_cycle()

    # Assert oak_ave_ew_wave (P1, but higher score) is chosen
    agent_core.logger.info.assert_any_call("Sorted candidate corridors by priority: ['oak_ave_ew_wave' (Prio: 1, AvgScore: 0.80), 'main_st_ns_wave' (Prio: 1, AvgScore: -0.50)].")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'oak_ave_ew_wave'.")
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="oak_ave_ew_wave", config=oak_ave_cfg, signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)

    # Restore original priorities for other tests if configs are shared (they are module level)
    GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["priority"] = 1
    GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"]["priority"] = 2


# (Other existing tests should be checked to ensure they still pass with the new scoring logic if they trigger green waves)

# Test for _execute_green_wave sequence and timing (can be adapted from previous test file)
# This test is more involved due to mocking sleep and checking call order.
@patch('asyncio.sleep', new_callable=AsyncMock)
@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_execute_green_wave_detailed_sequence_main_st(mock_dt, mock_sleep, agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service): # Renamed fixture
    agent_core = agent_core_with_patched_logger_and_persistence # Use the fixture that has logger patched
    corridor_id = "main_st_ns_wave" # Use a specific corridor from the actual config
    config = GREEN_WAVE_CORRIDOR_CONFIGS[corridor_id] # agent_core.green_wave_corridor_configs[corridor_id]

    cycle_start_time = datetime(2023, 1, 1, 8, 0, 0)
    mock_dt.utcnow.return_value = cycle_start_time

    mock_signal_states_dict = {
        sid: SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1.0, longitude=1.0), main_flow_direction="NS")
        for sid in config["signals_in_order"]
    }
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration_seconds: \
        asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=mock_dt.utcnow.return_value))

    processed_coords = set()

    await agent_core._execute_green_wave(
        corridor_id=corridor_id,
        signals_in_order=config["signals_in_order"],
        green_phase=config["target_green_phase"],
        green_time_seconds=config["wave_green_time_seconds"],
        offsets_seconds=config["offsets_seconds"],
        all_current_signal_states=mock_signal_states_dict,
        processed_signals_for_coordination=processed_coords,
        now_utc=cycle_start_time
    )

    expected_calls_to_set_phase = []
    current_mock_time = cycle_start_time
    for i, signal_id in enumerate(config["signals_in_order"]):
        expected_calls_to_set_phase.append(
            call(signal_id, config["target_green_phase"], config["wave_green_time_seconds"])
        )
        # Simulate time progression for datetime.utcnow() calls within _execute_green_wave loop
        # This part is crucial for asserting sleep durations accurately.
        if i < len(config["signals_in_order"]) -1 : # If not the last signal
            # Time of command for current signal
            command_time_current_signal = cycle_start_time + timedelta(seconds=config["offsets_seconds"][i])
            # Expected target time for next signal
            target_time_next_signal = cycle_start_time + timedelta(seconds=config["offsets_seconds"][i+1])
            # The sleep should be called to bridge the gap from command_time_current_signal (or slightly after due to processing)
            # to target_time_next_signal.
            # In _execute_green_wave, delay is (target_command_time_for_this_signal - datetime.utcnow())
            # So, for the *next* iteration's delay calculation, datetime.utcnow() will be *after* the current command.
            # This means the mock_dt.utcnow should be advanced here.
            mock_dt.utcnow.return_value = command_time_current_signal # Simulate time at point of current command dispatch

    mock_traffic_signal_service.set_signal_phase.assert_has_calls(expected_calls_to_set_phase, any_order=False)
    assert len(processed_coords) == len(config["signals_in_order"]) # All signals should be processed
    # Asserting sleep call values requires more intricate mocking of time passing *during* _execute_green_wave.
    # The number of sleep calls should be at least len(config["signals_in_order"]) - 1 if offsets are increasing and non-zero.
    if len(config["signals_in_order"]) > 1 and config["offsets_seconds"][0] < config["offsets_seconds"][1] : # Simplified check
        assert mock_sleep.call_count >= len(config["signals_in_order"]) -1
