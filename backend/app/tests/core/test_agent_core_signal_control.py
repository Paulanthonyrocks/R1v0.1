import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Imports from the application
from app.core.agent_core import AgentCore
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.tasks.prediction_scheduler import PredictionScheduler # Corrected import
from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalOperationalStatusEnum,
    SignalControlCommandResponse, SignalControlStatusEnum
)
from app.models.traffic import LocationModel
# from app.models.websocket import UserSpecificConditionAlert # Not directly used by these tests

# Configure basic logging for test output if necessary
# logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    # Default: Low congestion, no critical alerts, no active incident alerts
    service.get_current_system_kpis_summary = MagicMock(return_value={"overall_congestion_level": "LOW"})
    service.get_critical_alert_summary = AsyncMock(return_value={
        "critical_unack_alert_count": 0,
        "recent_critical_types": [],
        "active_alerts": [] # Default to no active alerts
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
            signal_id="test_signal_id", # Default signal_id
            status=SignalControlStatusEnum.ACCEPTED,
            message="Command accepted.",
            timestamp=datetime.utcnow() # Will be patched in time-sensitive tests
        )
    )
    return service

@pytest.fixture
def agent_core_with_patched_logger( # Renamed for clarity when not patching datetime
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
        return core

# --- Existing Test Cases (Ensure they still pass or adapt them) ---

@pytest.mark.asyncio
async def test_no_signal_control_when_congestion_low(agent_core_with_patched_logger, mock_traffic_signal_service):
    # Arrange: Default mock_analytics_service has LOW congestion
    agent_core = agent_core_with_patched_logger # Use the correctly named fixture

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    # Check for the log specific to general congestion not being HIGH
    agent_core.logger.info.assert_any_call("System congestion level (LOW) is not HIGH. No general autonomous system-wide signal adjustments made by AgentCore.")


@pytest.mark.asyncio
async def test_no_signal_control_when_high_congestion_but_no_online_signals(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    offline_signal = SignalState(signal_id="sig_offline", current_phase=SignalPhaseEnum.OFF, operational_status=SignalOperationalStatusEnum.OFFLINE, last_updated=datetime.utcnow())
    mock_traffic_signal_service.get_all_signal_states.return_value = [offline_signal]

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("High congestion: No traffic signals required general intervention or were suitable for autonomous GREEN phase change this cycle (considering incident responses and cooldowns).")


@pytest.mark.asyncio
async def test_high_congestion_one_online_red_signal_is_controlled_general(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    online_red_signal = SignalState(signal_id="sig_1", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1,longitude=1,name="Sig1"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [online_red_signal]

    # Specific mock for this call to track its details
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id="sig_1", status=SignalControlStatusEnum.ACCEPTED, message="Set to GREEN by general", timestamp=datetime.utcnow()
    )

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_1", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    assert "sig_1" in agent_core._recent_signal_actions
    assert agent_core._recent_signal_actions["sig_1"]['reason'] == 'general_congestion'
    agent_core.logger.info.assert_any_call("General Congestion Signal control response for sig_1: Status='accepted', Message='Set to GREEN by general'")


@pytest.mark.asyncio
async def test_high_congestion_online_signal_already_green_general(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    online_green_signal = SignalState(signal_id="sig_green", current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
    mock_traffic_signal_service.get_all_signal_states.return_value = [online_green_signal]

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call(
        "No action for signal sig_green (general congestion): Status: ONLINE, Phase: GREEN. "
    )


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_general_congestion_skip_recently_controlled_signal_control_next(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now

    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    signal1 = SignalState(signal_id="sig_recent_gen", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1,longitude=1,name="SigRecentGen"))
    signal2 = SignalState(signal_id="sig_next_gen", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=2,longitude=2,name="SigNextGen"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal1, signal2]

    agent_core._recent_signal_actions[signal1.signal_id] = {
        'timestamp': now - timedelta(seconds=30),
        'phase_commanded': SignalPhaseEnum.GREEN, 'duration_commanded': 60, 'reason': 'general_congestion'
    }
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id=signal2.signal_id, status=SignalControlStatusEnum.ACCEPTED, message="Set sig_next_gen to GREEN", timestamp=now
    )

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id=signal2.signal_id, phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    assert signal2.signal_id in agent_core._recent_signal_actions
    agent_core.logger.info.assert_any_call(f"Signal {signal1.signal_id} was recently acted upon. Skipping for general control. Details: {{'timestamp': {now - timedelta(seconds=30)}, 'phase_commanded': <SignalPhaseEnum.GREEN: 'GREEN'>, 'duration_commanded': 60, 'reason': 'general_congestion'}}")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_cooldown_cleanup_general(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    current_time = datetime(2023, 1, 1, 12, 5, 0) # Advanced time
    mock_dt.utcnow.return_value = current_time

    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    agent_core._recent_signal_actions["sig_active_cooldown_gen"] = {'timestamp': current_time - timedelta(seconds=30), 'reason': 'general_congestion'}
    expired_ts = current_time - timedelta(seconds=agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS + 60)
    agent_core._recent_signal_actions["sig_expired_cooldown_gen"] = {'timestamp': expired_ts, 'reason': 'general_congestion'}

    controllable_sig = SignalState(signal_id="sig_target_gen", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=current_time, location=LocationModel(latitude=3,longitude=3,name="TargetGen"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [controllable_sig] # Only one controllable signal available

    await agent_core.run_decision_cycle()

    assert "sig_active_cooldown_gen" in agent_core._recent_signal_actions
    assert "sig_expired_cooldown_gen" not in agent_core._recent_signal_actions
    agent_core.logger.debug.assert_any_call("Removed 1 old entries from recent signal actions. Kept 1.")
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_target_gen", phase=SignalPhaseEnum.GREEN, duration_seconds=60)


# --- New Test Cases for Incident Response ---

@pytest.mark.asyncio
async def test_no_active_incidents_no_incident_control_triggered(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    # Default fixture for mock_analytics_service returns empty active_alerts
    await agent_core.run_decision_cycle()
    # No set_signal_phase calls should be made due to incidents
    # We rely on set_signal_phase mock having specific return values if we want to check call_args for reason
    # For this test, just ensure it's not called if no incidents or general congestion
    if agent_core.analytics_service.get_current_system_kpis_summary()["overall_congestion_level"] != "HIGH":
        mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("No active individual critical alerts to process for incident response.")


@pytest.mark.asyncio
async def test_incident_alert_no_nearby_signals(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": "acc1", "type": "ACCIDENT", "location": {"latitude": 10.0, "longitude": 10.0, "name": "Far Crash"}}]
    }
    # Signals are at (1,1), (2,2) etc. by default in other tests if added to mock_traffic_signal_service
    mock_traffic_signal_service.get_all_signal_states.return_value = [
        SignalState(signal_id="sig_far_away", location=LocationModel(latitude=1.0, longitude=1.0), operational_status=SignalOperationalStatusEnum.ONLINE)
    ]
    # Alternative: mock _find_signals_near_location directly
    # agent_core._find_signals_near_location = AsyncMock(return_value=[])

    await agent_core.run_decision_cycle()
    # Check that set_signal_phase was not called for an incident reason
    # This is tricky if general congestion also runs. Ensure general congestion is LOW.
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "LOW"}
    await agent_core.run_decision_cycle() # Re-run with low congestion

    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("No nearby signals found within 250m for incident 'acc1' (type: ACCIDENT).")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_incident_accident_one_nearby_signal_controlled(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now

    alert_id = "acc_nearby_001"
    signal_id_A = "sig_A_incident"
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ACCIDENT", "location": {"latitude": 1.0001, "longitude": 1.0001, "name": "Nearby Crash"}}]
    }
    signal_A = SignalState(signal_id=signal_id_A, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0, name="Signal A"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_A]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id=signal_id_A, status=SignalControlStatusEnum.ACCEPTED, timestamp=now
    )

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id=signal_id_A, phase=SignalPhaseEnum.GREEN, duration_seconds=90
    )
    assert signal_id_A in agent_core._recent_signal_actions
    action = agent_core._recent_signal_actions[signal_id_A]
    assert action['reason'] == 'incident_response_ACCIDENT'
    assert action['incident_id'] == alert_id
    assert action['duration_commanded'] == 90
    # To verify it's in processed_signals_for_incident, we'd need another signal and check logs, or inspect state if possible
    # For now, the fact it's in _recent_signal_actions with incident reason is a strong indicator.


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_incident_road_closure_nearby_signal_placeholder(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now
    alert_id = "rc_nearby_001"
    signal_id_B = "sig_B_closure"

    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ROAD_CLOSURE", "location": {"latitude": 1.0001, "longitude": 1.0001, "name": "Nearby Closure"}}]
    }
    signal_B = SignalState(signal_id=signal_id_B, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0, name="Signal B"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_B]

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_not_called() # Placeholder does not call set_signal_phase
    agent_core.logger.info.assert_any_call(f"ROAD_CLOSURE strategy for '{signal_id_B}': Placeholder action. Recording to prevent general override.")
    assert signal_id_B in agent_core._recent_signal_actions # Check if it's recorded
    action = agent_core._recent_signal_actions[signal_id_B]
    assert action['reason'] == 'incident_response_ROAD_CLOSURE'
    assert action['incident_id'] == alert_id


@pytest.mark.asyncio
async def test_incident_alert_skips_offline_signal(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_critical_alert_summary.return_value = {
         "active_alerts": [{"alert_id": "acc_offline_sig", "type": "ACCIDENT", "location": {"latitude": 1.0, "longitude": 1.0, "name": "Crash"}}]
    }
    offline_signal = SignalState(signal_id="sig_offline_inc", current_phase=SignalPhaseEnum.OFF, operational_status=SignalOperationalStatusEnum.OFFLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1.0, longitude=1.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [offline_signal]

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call("Signal 'sig_offline_inc' is not ONLINE, skipping for ACCIDENT response.")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_incident_alert_skips_signal_on_short_cooldown(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0)
    mock_dt.utcnow.return_value = now

    signal_id_C = "sig_C_short_cooldown"
    mock_analytics_service.get_critical_alert_summary.return_value = {
         "active_alerts": [{"alert_id": "acc_short_cooldown", "type": "ACCIDENT", "location": {"latitude": 1.0, "longitude": 1.0, "name": "Crash"}}]
    }
    signal_C = SignalState(signal_id=signal_id_C, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_C]

    # Simulate signal_C was acted upon 30s ago (within SIGNAL_ACTION_COOLDOWN_SECONDS)
    agent_core._recent_signal_actions[signal_id_C] = {
        'timestamp': now - timedelta(seconds=30),
        'reason': 'some_other_reason', 'phase_commanded': SignalPhaseEnum.GREEN, 'duration_commanded': 60
    }

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call(f"Signal '{signal_id_C}' on short cooldown ({agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS}s) due to reason 'some_other_reason'. Skipping for ACCIDENT 'acc_short_cooldown'.")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_incident_priority_over_general_congestion(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0)
    mock_dt.utcnow.return_value = now

    signal_id_D = "sig_D_incident_priority"
    alert_id = "acc_priority"
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    mock_analytics_service.get_critical_alert_summary.return_value = {
         "active_alerts": [{"alert_id": alert_id, "type": "ACCIDENT", "location": {"latitude": 1.0, "longitude": 1.0, "name": "Crash"}}]
    }
    signal_D = SignalState(signal_id=signal_id_D, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_D]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id=signal_id_D, status=SignalControlStatusEnum.ACCEPTED, timestamp=now
    )

    await agent_core.run_decision_cycle()

    # Called for incident
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id=signal_id_D, phase=SignalPhaseEnum.GREEN, duration_seconds=90
    )
    assert agent_core._recent_signal_actions[signal_id_D]['reason'] == f'incident_response_ACCIDENT'
    # General congestion logic should log that it was skipped
    agent_core.logger.debug.assert_any_call(f"Signal '{signal_id_D}' was already handled by incident-specific logic this cycle. Skipping for general congestion control.")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_general_congestion_respects_incident_cooldown(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    signal_id_E = "sig_E_incident_cd"
    alert_id_cycle1 = "acc_for_inc_cd_test"

    # --- Cycle 1: Incident occurs, TS001 put on INCIDENT_SIGNAL_COOLDOWN_SECONDS ---
    time_cycle1 = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = time_cycle1
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "LOW"} # No general congestion
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id_cycle1, "type": "ACCIDENT", "location": {"latitude": 1.0, "longitude": 1.0, "name": "Crash for Cooldown Test"}}]
    }
    signal_E = SignalState(signal_id=signal_id_E, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=time_cycle1, location=LocationModel(latitude=1.0, longitude=1.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_E]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id=signal_id_E, status=SignalControlStatusEnum.ACCEPTED, timestamp=time_cycle1
    )
    await agent_core.run_decision_cycle("user_cycle1")

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id=signal_id_E, phase=SignalPhaseEnum.GREEN, duration_seconds=90)
    assert agent_core._recent_signal_actions[signal_id_E]['incident_id'] == alert_id_cycle1
    original_action_timestamp = agent_core._recent_signal_actions[signal_id_E]['timestamp']

    # --- Cycle 2: Time advances (SIGNAL_ACTION_COOLDOWN < elapsed < INCIDENT_SIGNAL_COOLDOWN), High Congestion, No Incidents ---
    time_cycle2 = time_cycle1 + timedelta(seconds=agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS + 30) # e.g., 150s later
    mock_dt.utcnow.return_value = time_cycle2
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    mock_analytics_service.get_critical_alert_summary.return_value = {"active_alerts": []} # No new incidents
    # Signal E is still GREEN from incident, or reset it to RED to see if general congestion tries to act
    signal_E.current_phase = SignalPhaseEnum.RED
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_E]
    mock_traffic_signal_service.set_signal_phase.reset_mock() # Reset call count from cycle 1

    await agent_core.run_decision_cycle("user_cycle2")

    mock_traffic_signal_service.set_signal_phase.assert_not_called() # Should be skipped
    expected_log_reason = f"incident response (ID: {alert_id_cycle1}, Type: ACCIDENT)"
    agent_core.logger.debug.assert_any_call(
        f"Signal '{signal_id_E}' on cooldown. Last action for '{expected_log_reason}' "
        f"at {original_action_timestamp.strftime('%Y-%m-%d %H:%M:%S')} ({(time_cycle2 - original_action_timestamp).total_seconds():.0f}s ago). "
        f"Cooldown is {agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS}s. Skipping for general control."
    )

@pytest.mark.asyncio
async def test_incident_alert_invalid_location_data_skipped(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [
            {"alert_id": "bad_loc1", "type": "ACCIDENT", "location": None},
            {"alert_id": "bad_loc2", "type": "FIRE", "location": {"latitude": "invalid_float", "longitude": 1.0}}
        ]
    }
    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.warning.assert_any_call("Alert bad_loc1 (type: ACCIDENT) missing valid location data: None. Skipping.")
    agent_core.logger.error.assert_any_call("Could not parse location for alert bad_loc2: {'latitude': 'invalid_float', 'longitude': 1.0}. Error: %s", ANY) # ANY from unittest.mock if needed, or check for part of error string


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_multiple_incident_alerts_processed_correctly(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    from unittest.mock import ANY # For more flexible error message checking if needed
    agent_core = agent_core_with_patched_logger
    now = datetime(2023,1,1,12,0,0)
    mock_dt.utcnow.return_value = now

    alert1_id, signal1_id = "acc_multi_1", "sig_F_multi"
    alert2_id, signal2_id = "acc_multi_2", "sig_G_multi"

    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [
            {"alert_id": alert1_id, "type": "ACCIDENT", "location": {"latitude": 1.0, "longitude": 1.0, "name": "Crash F"}},
            {"alert_id": alert2_id, "type": "ACCIDENT", "location": {"latitude": 2.0, "longitude": 2.0, "name": "Crash G"}}
        ]
    }
    signal_F = SignalState(signal_id=signal1_id, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0))
    signal_G = SignalState(signal_id=signal2_id, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=2.0, longitude=2.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_F, signal_G]

    # Mock set_signal_phase to return success for any call
    mock_traffic_signal_service.set_signal_phase.side_effect = lambda signal_id, phase, duration_seconds: asyncio.Future.resolve(
        SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, timestamp=now)
    )

    await agent_core.run_decision_cycle()

    assert mock_traffic_signal_service.set_signal_phase.call_count == 2
    mock_traffic_signal_service.set_signal_phase.assert_any_call(signal_id=signal1_id, phase=SignalPhaseEnum.GREEN, duration_seconds=90)
    mock_traffic_signal_service.set_signal_phase.assert_any_call(signal_id=signal2_id, phase=SignalPhaseEnum.GREEN, duration_seconds=90)

    assert agent_core._recent_signal_actions[signal1_id]['incident_id'] == alert1_id
    assert agent_core._recent_signal_actions[signal2_id]['incident_id'] == alert2_id
