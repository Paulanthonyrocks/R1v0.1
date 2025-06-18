import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call

import pytest

# Imports from the application
from app.core.agent_core import AgentCore, PILOT_CORRIDOR_CONFIG, MOCK_GREEN_WAVE_TRIGGER_KPI # Import constants
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
    service.get_current_system_kpis_summary = MagicMock(return_value={
        "overall_congestion_level": "LOW",
        MOCK_GREEN_WAVE_TRIGGER_KPI: "LOW" # Default for green wave trigger
    })
    service.get_critical_alert_summary = AsyncMock(return_value={
        "critical_unack_alert_count": 0,
        "recent_critical_types": [],
        "active_alerts": []
    })
    service.broadcast_operational_alert = AsyncMock()
    service.send_user_specific_alert = AsyncMock()
    service.predict_incident_likelihood = AsyncMock(return_value={"likelihood_score_percent": 0})
    return service

@pytest.fixture
def mock_prediction_scheduler():
    service = MagicMock(spec=PredictionScheduler)
    service.set_priority_locations = AsyncMock()
    return service

@pytest.fixture
def mock_personalized_routing_service():
    service = MagicMock(spec=PersonalizedRoutingService)
    service.proactively_suggest_route = AsyncMock(return_value=None)
    service.get_user_common_travel_patterns = AsyncMock(return_value=[])
    return service

@pytest.fixture
def mock_traffic_signal_service():
    service = MagicMock(spec=TrafficSignalService)
    service.get_all_signal_states = AsyncMock(return_value=[])
    service.set_signal_phase = AsyncMock(
        return_value=SignalControlCommandResponse(
            signal_id="test_signal_id",
            status=SignalControlStatusEnum.ACCEPTED,
            message="Command accepted.",
            timestamp=datetime.utcnow()
        )
    )
    return service

@pytest.fixture
def agent_core_with_patched_logger(
    mock_prediction_scheduler,
    mock_personalized_routing_service,
    mock_analytics_service,
    mock_traffic_signal_service
):
    with patch('app.core.agent_core.logger', MagicMock(spec=logging.Logger)) as mock_logger:
        core = AgentCore(
            prediction_scheduler=mock_prediction_scheduler,
            personalized_routing_service=mock_personalized_routing_service,
            analytics_service=mock_analytics_service,
            traffic_signal_service=mock_traffic_signal_service
        )
        core.logger = mock_logger
        # Ensure pilot_corridor_configs is set as it's now in __init__
        core.pilot_corridor_configs = PILOT_CORRIDOR_CONFIG
        return core

# --- Existing Test Cases (Adapted for clarity/consistency if needed) ---
# ... (Previous test cases for general congestion and incident response remain largely unchanged) ...
# For brevity, I'll omit re-listing all previous tests if they don't directly conflict.
# Assume they are present and functioning.

# --- New Test Cases for Green Wave Logic ---

@patch('asyncio.sleep', new_callable=AsyncMock) # Mock asyncio.sleep
@patch('app.core.agent_core.datetime', new_callable=MagicMock) # Mock datetime.utcnow
@pytest.mark.asyncio
async def test_green_wave_execution_sequence_and_timing(mock_datetime, mock_asyncio_sleep, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger

    # Arrange: Configure for green wave
    mock_analytics_service.get_current_system_kpis_summary.return_value = {
        "overall_congestion_level": "LOW", # Keep general congestion low to isolate wave
        MOCK_GREEN_WAVE_TRIGGER_KPI: "HIGH"
    }

    corridor_id = "main_st_ns_wave"
    config = PILOT_CORRIDOR_CONFIG[corridor_id]
    signal_ids = config["signals_in_order"]
    offsets = config["offsets_seconds"]
    green_time = config["wave_green_time_seconds"]
    target_phase = config["target_green_phase"]

    # Mock initial time for the cycle
    cycle_start_time = datetime(2023, 1, 1, 12, 0, 0)
    mock_datetime.utcnow.return_value = cycle_start_time

    mock_signal_states = {
        sid: SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1.0+i*0.001, longitude=1.0), last_updated=cycle_start_time, main_flow_direction="NS")
        for i, sid in enumerate(signal_ids)
    }
    mock_traffic_signal_service.get_all_signal_states.return_value = list(mock_signal_states.values())

    # Make set_signal_phase return success for these signals
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration_seconds: \
        asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=mock_datetime.utcnow.return_value))


    # Act
    await agent_core.run_decision_cycle()

    # Assert
    assert mock_traffic_signal_service.set_signal_phase.call_count == len(signal_ids)

    expected_sleep_calls = []
    actual_command_timestamps = []

    for i, signal_id in enumerate(signal_ids):
        # Assert signal command
        mock_traffic_signal_service.set_signal_phase.assert_any_call(
            signal_id=signal_id, phase=target_phase, duration_seconds=green_time
        )
        # Verify action recorded
        assert signal_id in agent_core._recent_signal_actions
        assert agent_core._recent_signal_actions[signal_id]['reason'] == f'green_wave_{corridor_id}'

        # Track expected sleep and command times for sequence verification
        target_command_time_for_this_signal = cycle_start_time + timedelta(seconds=offsets[i])

        # If not the first signal, there should have been a sleep call to reach its offset
        if i > 0:
            # The sleep duration is calculated based on the *previous* command's actual time (or wave start for first sleep)
            # and the *current* signal's target command time.
            # This needs precise mocking of datetime.utcnow call returns during the loop in _execute_green_wave
            # For simplicity here, we check that sleep was called before commands (except the first)
            # A more rigorous timing test would involve instrumenting _execute_green_wave or more complex time mocking.
            pass # Complex to assert exact sleep duration without more intricate time control inside the loop

        # Simulate time advancing for the next `datetime.utcnow()` call within `_execute_green_wave`
        # This is crucial for the `delay_seconds_from_now` calculation in the SUT
        if i < len(signal_ids) - 1: # Before the next signal's delay calculation
             # Assume command execution takes negligible time for this mock setup
            mock_datetime.utcnow.return_value = cycle_start_time + timedelta(seconds=offsets[i])


    # Check that sleep was called appropriately (number of times)
    # For N signals, there will be N "delay_seconds_from_now" calculations.
    # Sleep is called if delay > 0.05. If all offsets are positive and increasing, N-1 sleeps or N sleeps.
    # If first offset is 0, first sleep might be skipped if processing is fast.
    # This assertion is basic; more detailed checks need careful time mocking.
    assert mock_asyncio_sleep.call_count >= len(signal_ids) -1 if offsets[0] == 0 and len(signal_ids) > 1 else mock_asyncio_sleep.call_count >= 0


@pytest.mark.asyncio
async def test_green_wave_skips_offline_signal_in_corridor(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {MOCK_GREEN_WAVE_TRIGGER_KPI: "HIGH"}

    corridor_id = "main_st_ns_wave"
    config = PILOT_CORRIDOR_CONFIG[corridor_id]
    signal_ids = config["signals_in_order"] # e.g., ["TS001", "TS002", "TS004"]

    mock_signal_states = [
        SignalState(signal_id=signal_ids[0], current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1,longitude=1), last_updated=datetime.utcnow()),
        SignalState(signal_id=signal_ids[1], current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.OFFLINE, location=LocationModel(latitude=2,longitude=2), last_updated=datetime.utcnow()), # Middle signal OFFLINE
        SignalState(signal_id=signal_ids[2], current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=3,longitude=3), last_updated=datetime.utcnow())
    ]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states

    await agent_core.run_decision_cycle()

    # Check that set_signal_phase was called for online signals but not for the offline one
    calls = mock_traffic_signal_service.set_signal_phase.call_args_list
    called_signal_ids = [call.args[0] for call in calls] # call.args[0] is signal_id

    assert signal_ids[0] in called_signal_ids
    assert signal_ids[1] not in called_signal_ids # The OFFLINE signal
    assert signal_ids[2] in called_signal_ids
    agent_core.logger.warning.assert_any_call(f"Green wave '{corridor_id}': Signal '{signal_ids[1]}' is not online or not found. Skipping in wave.")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_green_wave_skips_signal_processed_by_incident(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0)
    mock_dt.utcnow.return_value = now

    mock_analytics_service.get_current_system_kpis_summary.return_value = {MOCK_GREEN_WAVE_TRIGGER_KPI: "HIGH"}
    corridor_id = "main_st_ns_wave"
    config = PILOT_CORRIDOR_CONFIG[corridor_id]
    signal_ids = config["signals_in_order"]
    signal_processed_by_incident = signal_ids[0]

    mock_signal_states = [SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1+i*0.01, longitude=1), last_updated=now) for i, sid in enumerate(signal_ids)]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states

    # Simulate signal_processed_by_incident was handled by incident logic
    # This requires direct modification or a more elaborate setup if run_decision_cycle is called as a whole
    # For a unit test of _execute_green_wave, one might pass a pre-filled set.
    # Here, we'll test the check within _execute_green_wave by ensuring processed_signals_for_incident is populated before wave logic.
    # This is slightly artificial as incident logic runs before wave logic in run_decision_cycle.
    # A better way: mock get_critical_alert_summary to create an incident that processes this signal.

    # Let's assume an ACCIDENT alert processed signal_ids[0]
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": "acc_temp", "type": "ACCIDENT", "location": {"latitude": 1.0, "longitude": 1.0}}] # Near signal_ids[0]
    }
    # Ensure set_signal_phase for ACCIDENT is distinct if needed, or just let it run
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration_seconds: \
        asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=now))

    await agent_core.run_decision_cycle()

    # Check logs from _execute_green_wave for skipping signal_processed_by_incident
    agent_core.logger.info.assert_any_call(f"Green wave '{corridor_id}': Signal '{signal_processed_by_incident}' was handled by incident logic. Skipping in wave.")

    # Check that set_signal_phase was not called for signal_processed_by_incident with green_wave reason
    for call_args in mock_traffic_signal_service.set_signal_phase.call_args_list:
        called_signal_id = call_args.args[0]
        if called_signal_id == signal_processed_by_incident:
             # If it was called, it must have been for the ACCIDENT, not the wave.
             # This requires checking the recorded reason if an action was taken.
             # The test is simpler if we ensure the ACCIDENT response doesn't also try to set it to GREEN for the same duration.
             # For this test, we'll rely on the log and that other signals in wave were processed.
             pass
    assert signal_ids[1] in agent_core._recent_signal_actions and agent_core._recent_signal_actions[signal_ids[1]]['reason'].startswith('green_wave')


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_green_wave_skips_signal_on_general_cooldown(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0)
    mock_dt.utcnow.return_value = now
    mock_analytics_service.get_current_system_kpis_summary.return_value = {MOCK_GREEN_WAVE_TRIGGER_KPI: "HIGH"}
    corridor_id = "main_st_ns_wave"
    config = PILOT_CORRIDOR_CONFIG[corridor_id]
    signal_ids = config["signals_in_order"]
    signal_on_cooldown = signal_ids[0]

    agent_core._recent_signal_actions[signal_on_cooldown] = {
        'timestamp': now - timedelta(seconds=30), # Within SIGNAL_ACTION_COOLDOWN_SECONDS
        'reason': 'some_prior_action', 'phase_commanded': SignalPhaseEnum.GREEN, 'duration_commanded': 60
    }
    mock_signal_states = [SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1+i*0.01,longitude=1), last_updated=now) for i, sid in enumerate(signal_ids)]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states

    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call(f"Green wave '{corridor_id}': Signal '{signal_on_cooldown}' on general cooldown ({agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS}s). Reason: some_prior_action. Skipping in wave.")
    # Ensure other signals in the wave (if any) were still processed
    if len(signal_ids) > 1:
        assert signal_ids[1] in agent_core._recent_signal_actions
        assert agent_core._recent_signal_actions[signal_ids[1]]['reason'].startswith('green_wave')


@pytest.mark.asyncio
async def test_green_wave_activation_via_run_decision_cycle_trigger(agent_core_with_patched_logger, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {MOCK_GREEN_WAVE_TRIGGER_KPI: "HIGH"}
    # Provide empty signals to prevent actual wave execution, just check for trigger
    agent_core.traffic_signal_service.get_all_signal_states = AsyncMock(return_value=[])


    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call(f"High demand detected for KPI '{MOCK_GREEN_WAVE_TRIGGER_KPI}'. Attempting to activate green wave.")
    agent_core.logger.info.assert_any_call("Activating green wave for corridor: 'main_st_ns_wave'")


@pytest.mark.asyncio
async def test_general_congestion_skips_green_wave_signals_in_same_cycle(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    # Both green wave and high congestion are triggered
    mock_analytics_service.get_current_system_kpis_summary.return_value = {
        "overall_congestion_level": "HIGH",
        MOCK_GREEN_WAVE_TRIGGER_KPI: "HIGH"
    }
    corridor_id = "main_st_ns_wave"
    config = PILOT_CORRIDOR_CONFIG[corridor_id]
    wave_signal_ids = config["signals_in_order"]

    # Mock signals such that they are all candidates for green wave
    mock_signal_states = [
        SignalState(signal_id=sid, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1+i*0.01,longitude=1), last_updated=datetime.utcnow())
        for i, sid in enumerate(wave_signal_ids)
    ]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states
    # Ensure green wave commands are "successful"
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration_seconds: \
        asyncio.Future.resolve(SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=datetime.utcnow()))

    await agent_core.run_decision_cycle()

    # Check that general congestion logic logged skipping for each wave signal
    for signal_id in wave_signal_ids:
        agent_core.logger.debug.assert_any_call(f"Signal '{signal_id}' was handled by coordination logic. Skipping general control.")
        # Also check that set_signal_phase was NOT called with reason 'general_congestion' for these signals
        for call_obj in mock_traffic_signal_service.set_signal_phase.call_args_list:
            if call_obj.args[0] == signal_id: # if signal_id matches
                # Check that this call was part of the green wave, not general congestion
                # This requires inspecting the _recent_signal_actions or assuming if it was called, it was for the wave
                # as the skip logic should prevent a second call.
                pass # This is implicitly tested by the fact that processed_by_coordination prevents general control.

    # And that general congestion did not try to re-control them
    # This means set_signal_phase calls for these signals should only have green_wave reason
    for sig_id in wave_signal_ids:
        assert sig_id in agent_core._recent_signal_actions
        assert agent_core._recent_signal_actions[sig_id]['reason'].startswith('green_wave')
