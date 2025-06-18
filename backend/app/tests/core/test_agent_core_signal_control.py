import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY

import pytest

# Imports from the application
from app.core.agent_core import AgentCore
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
    service.get_current_system_kpis_summary = MagicMock(return_value={"overall_congestion_level": "LOW"})
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
        return core

# --- Existing Test Cases (Adapted for clarity/consistency if needed) ---

@pytest.mark.asyncio
async def test_no_signal_control_when_congestion_low(agent_core_with_patched_logger, mock_traffic_signal_service):
    agent_core = agent_core_with_patched_logger
    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
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
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id="sig_1", status=SignalControlStatusEnum.ACCEPTED, message="Set to GREEN by general", timestamp=datetime.utcnow()
    )
    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_1", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    assert "sig_1" in agent_core._recent_signal_actions
    assert agent_core._recent_signal_actions["sig_1"]['reason'] == 'general_congestion'

@pytest.mark.asyncio
async def test_high_congestion_online_signal_already_green_general(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    online_green_signal = SignalState(signal_id="sig_green", current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
    mock_traffic_signal_service.get_all_signal_states.return_value = [online_green_signal]
    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call(f"No action for signal {online_green_signal.signal_id} (general congestion): Status: {online_green_signal.operational_status.value}, Phase: {online_green_signal.current_phase.value}. ")


# --- New/Modified Test Cases for Incident and ROAD_CLOSURE ---

@pytest.mark.asyncio
async def test_no_active_incidents_no_incident_control_triggered(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    # Default fixture returns empty active_alerts
    await agent_core.run_decision_cycle()
    agent_core.logger.info.assert_any_call("No active individual critical alerts to process for incident response.")
    # Further check that set_signal_phase wasn't called for any incident reason
    calls = mock_traffic_signal_service.set_signal_phase.call_args_list
    for call in calls:
        kwargs = call.kwargs
        assert kwargs.get('reason') is None or not kwargs['reason'].startswith("incident_response")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_incident_accident_one_nearby_signal_controlled(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now
    alert_id = "acc_nearby_001"
    signal_id_A = "sig_A_incident"
    mock_analytics_service.get_critical_alert_summary.return_value = { "active_alerts": [{"alert_id": alert_id, "type": "ACCIDENT", "location": {"latitude": 1.0001, "longitude": 1.0001, "name": "Nearby Crash"}}]}
    signal_A = SignalState(signal_id=signal_id_A, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0, name="Signal A"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_A]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(signal_id=signal_id_A, status=SignalControlStatusEnum.ACCEPTED, timestamp=now)

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id=signal_id_A, phase=SignalPhaseEnum.GREEN, duration_seconds=90)
    action = agent_core._recent_signal_actions[signal_id_A]
    assert action['reason'] == 'incident_response_ACCIDENT'
    assert action['incident_id'] == alert_id


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_road_closure_nearby_green_signal_set_to_red(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now
    alert_id = "rc_001"
    signal_id_A = "sig_A_rc"

    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ROAD_CLOSURE", "location": {"latitude": 1.00001, "longitude": 1.00001, "name": "Closure at SigA"}}]
    }
    # Signal is ONLINE and GREEN, very close to the closure
    signal_A = SignalState(signal_id=signal_id_A, current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0, name="Signal A"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_A]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(signal_id=signal_id_A, status=SignalControlStatusEnum.ACCEPTED, timestamp=now)

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id=signal_id_A, phase=SignalPhaseEnum.RED, duration_seconds=agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS
    )
    assert signal_id_A in agent_core._recent_signal_actions
    action = agent_core._recent_signal_actions[signal_id_A]
    assert action['reason'] == 'incident_response_ROAD_CLOSURE'
    assert action['incident_id'] == alert_id
    assert action['phase_commanded'] == SignalPhaseEnum.RED
    # Check if it was added to processed_signals_for_incident (indirectly, by ensuring general congestion would skip it if active)
    # This requires a more complex test or direct inspection if the set was a member of the class.
    # For now, the recording in _recent_signal_actions is the primary check.


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_road_closure_nearby_signal_already_red(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now
    alert_id = "rc_002"
    signal_id_B = "sig_B_rc_already_red"

    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ROAD_CLOSURE", "location": {"latitude": 1.00001, "longitude": 1.00001, "name": "Closure at SigB"}}]
    }
    signal_B = SignalState(signal_id=signal_id_B, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0, name="Signal B"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_B]

    await agent_core.run_decision_cycle()

    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call(f"ROAD_CLOSURE strategy for '{signal_id_B}': Signal not GREEN (is {SignalPhaseEnum.RED.value}). No phase change needed, but marking as processed for incident.")
    # Check it's marked as processed by being in _recent_signal_actions with the road closure reason
    assert signal_id_B in agent_core._recent_signal_actions
    action = agent_core._recent_signal_actions[signal_id_B]
    assert action['reason'] == 'incident_response_ROAD_CLOSURE'
    assert action['incident_id'] == alert_id


@pytest.mark.asyncio
async def test_road_closure_signal_outside_radius_not_affected(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "LOW"} # Ensure no general congestion
    alert_id = "rc_003"
    signal_id_C = "sig_C_rc_far"
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ROAD_CLOSURE", "location": {"latitude": 10.0, "longitude": 10.0, "name": "Far Away Closure"}}] # Far from signal
    }
    # Signal C is at 1.0, 1.0, well outside ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS (50m)
    signal_C = SignalState(signal_id=signal_id_C, current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1.0, longitude=1.0, name="Signal C"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_C]

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call(f"No nearby signals found within {agent_core.ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS}m for incident '{alert_id}' (type: ROAD_CLOSURE).")


@pytest.mark.asyncio
async def test_road_closure_skips_offline_signal_in_radius(agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    alert_id = "rc_004"
    signal_id_D = "sig_D_rc_offline"
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ROAD_CLOSURE", "location": {"latitude": 1.00001, "longitude": 1.00001, "name": "Closure at SigD"}}]
    }
    signal_D = SignalState(signal_id=signal_id_D, current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.OFFLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1.0, longitude=1.0, name="Signal D"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_D]

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call(f"Signal '{signal_id_D}' is not ONLINE, skipping for ROAD_CLOSURE response.")
    assert signal_id_D not in agent_core._recent_signal_actions # Should not be processed or added to recent actions if offline


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_road_closure_respects_general_cooldown_for_target_signal(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    now = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = now
    alert_id = "rc_005"
    signal_id_E = "sig_E_rc_cooldown"

    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id, "type": "ROAD_CLOSURE", "location": {"latitude": 1.00001, "longitude": 1.00001, "name": "Closure at SigE"}}]
    }
    signal_E = SignalState(signal_id=signal_id_E, current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1.0, longitude=1.0, name="Signal E"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_E]

    # Pre-populate recent actions to simulate a recent general action
    agent_core._recent_signal_actions[signal_id_E] = {
        'timestamp': now - timedelta(seconds=30), # Within SIGNAL_ACTION_COOLDOWN_SECONDS
        'reason': 'general_congestion', 'phase_commanded': SignalPhaseEnum.GREEN, 'duration_commanded': 60
    }

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call(f"Signal '{signal_id_E}' on short cooldown ({agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS}s) due to reason 'general_congestion'. Skipping for ROAD_CLOSURE '{alert_id}'.")


@patch('app.core.agent_core.datetime')
@pytest.mark.asyncio
async def test_general_congestion_respects_road_closure_cooldown(mock_dt, agent_core_with_patched_logger, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger
    signal_id_F = "sig_F_rc_long_cd"
    alert_id_cycle1 = "rc_for_long_cd_test"

    # --- Cycle 1: ROAD_CLOSURE incident occurs, signal_id_F put on INCIDENT_SIGNAL_COOLDOWN_SECONDS ---
    time_cycle1 = datetime(2023, 1, 1, 12, 0, 0)
    mock_dt.utcnow.return_value = time_cycle1
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "LOW"} # No general congestion
    mock_analytics_service.get_critical_alert_summary.return_value = {
        "active_alerts": [{"alert_id": alert_id_cycle1, "type": "ROAD_CLOSURE", "location": {"latitude": 1.0, "longitude": 1.0, "name": "Closure for Long Cooldown Test"}}]
    }
    signal_F_state_green = SignalState(signal_id=signal_id_F, current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=time_cycle1, location=LocationModel(latitude=1.0, longitude=1.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_F_state_green]
    # Mock the specific call for ROAD_CLOSURE to RED
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id=signal_id_F, status=SignalControlStatusEnum.ACCEPTED, timestamp=time_cycle1
    )
    await agent_core.run_decision_cycle("user_cycle1_rc_cooldown")

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id=signal_id_F, phase=SignalPhaseEnum.RED, duration_seconds=agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS)
    assert agent_core._recent_signal_actions[signal_id_F]['reason'] == f'incident_response_ROAD_CLOSURE'
    assert agent_core._recent_signal_actions[signal_id_F]['incident_id'] == alert_id_cycle1
    original_action_timestamp = agent_core._recent_signal_actions[signal_id_F]['timestamp']

    # --- Cycle 2: Time advances (SIGNAL_ACTION_COOLDOWN < elapsed < INCIDENT_SIGNAL_COOLDOWN), High Congestion, No Incidents ---
    time_cycle2 = time_cycle1 + timedelta(seconds=agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS + 30) # e.g., 150s later
    mock_dt.utcnow.return_value = time_cycle2
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    mock_analytics_service.get_critical_alert_summary.return_value = {"active_alerts": []} # No new incidents
    # Signal F is now RED due to incident logic in Cycle 1
    signal_F_state_red = SignalState(signal_id=signal_id_F, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=time_cycle1, location=LocationModel(latitude=1.0, longitude=1.0))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_F_state_red]
    mock_traffic_signal_service.set_signal_phase.reset_mock()

    await agent_core.run_decision_cycle("user_cycle2_rc_cooldown")

    mock_traffic_signal_service.set_signal_phase.assert_not_called() # Should be skipped by general congestion
    expected_log_reason = f"incident response (ID: {alert_id_cycle1}, Type: ROAD_CLOSURE)"
    agent_core.logger.debug.assert_any_call(
        f"Signal '{signal_id_F}' on cooldown. Last action for '{expected_log_reason}' "
        f"at {original_action_timestamp.strftime('%Y-%m-%d %H:%M:%S')} ({(time_cycle2 - original_action_timestamp).total_seconds():.0f}s ago). "
        f"Cooldown is {agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS}s. Skipping for general control."
    )
