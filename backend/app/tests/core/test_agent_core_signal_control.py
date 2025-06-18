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
from app.tasks.prediction_scheduler import PredictionScheduler
from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalOperationalStatusEnum,
    SignalControlCommandResponse, SignalControlStatusEnum
)
from app.models.traffic import LocationModel
from app.models.websocket import UserSpecificConditionAlert # If needed by mocks

# Configure basic logging for test output if necessary
# logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    service.get_current_system_kpis_summary = MagicMock(return_value={"overall_congestion_level": "LOW"}) # Default
    service.get_critical_alert_summary = AsyncMock(return_value={"critical_unack_alert_count": 0, "recent_critical_types": []})
    # Add other async methods if they are called directly by tested logic and need AsyncMock
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
    service.get_all_signal_states = AsyncMock(return_value=[]) # Default to no signals
    service.set_signal_phase = AsyncMock( # Default successful response
        return_value=SignalControlCommandResponse(
            signal_id="test_signal_id",
            status=SignalControlStatusEnum.ACCEPTED,
            message="Command accepted.",
            timestamp=datetime.utcnow()
        )
    )
    return service

@pytest.fixture
def agent_core(
    mock_prediction_scheduler,
    mock_personalized_routing_service,
    mock_analytics_service,
    mock_traffic_signal_service
):
    # Patch logger inside AgentCore if its output is asserted or critical for debugging tests
    with patch('app.core.agent_core.logger', MagicMock(spec=logging.Logger)) as mock_logger:
        core = AgentCore(
            prediction_scheduler=mock_prediction_scheduler,
            personalized_routing_service=mock_personalized_routing_service,
            analytics_service=mock_analytics_service,
            traffic_signal_service=mock_traffic_signal_service
        )
        core.logger = mock_logger # Ensure the instance uses the patched logger
        return core

# --- Test Cases ---

@pytest.mark.asyncio
async def test_no_signal_control_when_congestion_low(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange: Default mock_analytics_service has LOW congestion

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("System congestion level (LOW) is not HIGH. No autonomous system-wide signal adjustments made by AgentCore.")

@pytest.mark.asyncio
async def test_no_signal_control_when_high_congestion_but_no_online_signals(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    offline_signal = SignalState(signal_id="sig_offline", current_phase=SignalPhaseEnum.OFF, operational_status=SignalOperationalStatusEnum.OFFLINE, last_updated=datetime.utcnow())
    mock_traffic_signal_service.get_all_signal_states.return_value = [offline_signal]

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("High congestion: No traffic signals required intervention or were suitable for autonomous GREEN phase change this cycle (considering cooldowns).")


@pytest.mark.asyncio
async def test_high_congestion_one_online_red_signal_is_controlled(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    online_red_signal = SignalState(signal_id="sig_1", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1,longitude=1,name="Sig1"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [online_red_signal]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id="sig_1", status=SignalControlStatusEnum.ACCEPTED, message="Set to GREEN", timestamp=datetime.utcnow()
    )

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_1", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    assert "sig_1" in agent_core._recent_signal_actions
    agent_core.logger.info.assert_any_call("Signal control response for sig_1: Status='accepted', Message='Set to GREEN'")


@pytest.mark.asyncio
async def test_high_congestion_online_signal_already_green(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    online_green_signal = SignalState(signal_id="sig_green", current_phase=SignalPhaseEnum.GREEN, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
    mock_traffic_signal_service.get_all_signal_states.return_value = [online_green_signal]

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.debug.assert_any_call(
        "No action for signal sig_green: Status: ONLINE, Phase: GREEN. Required: ONLINE and not GREEN. Or recently acted upon."
    )

@pytest.mark.asyncio
async def test_high_congestion_skip_recently_controlled_signal_control_next(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    signal1_id = "sig_recent"
    signal2_id = "sig_next"
    signal1 = SignalState(signal_id=signal1_id, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1,longitude=1,name="SigRecent"))
    signal2 = SignalState(signal_id=signal2_id, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=2,longitude=2,name="SigNext"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal1, signal2]

    # Simulate signal1 was recently acted upon
    recent_action_time = datetime.utcnow() - timedelta(seconds=30)
    agent_core._recent_signal_actions[signal1_id] = {
        'timestamp': recent_action_time,
        'phase_commanded': SignalPhaseEnum.GREEN,
        'duration_commanded': 60
    }
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id=signal2_id, status=SignalControlStatusEnum.ACCEPTED, message=f"Set {signal2_id} to GREEN", timestamp=datetime.utcnow()
    )

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id=signal2_id, phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    assert signal2_id in agent_core._recent_signal_actions
    agent_core.logger.info.assert_any_call(f"Signal {signal1_id} was recently acted upon. Skipping this cycle. Details: {{'timestamp': {recent_action_time}, 'phase_commanded': <SignalPhaseEnum.GREEN: 'GREEN'>, 'duration_commanded': 60}}")


@pytest.mark.asyncio
async def test_high_congestion_all_suitable_signals_on_cooldown(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    signal1 = SignalState(signal_id="sig_cd1", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
    now = datetime.utcnow()
    agent_core._recent_signal_actions["sig_cd1"] = {'timestamp': now - timedelta(seconds=30), 'phase_commanded': SignalPhaseEnum.GREEN, 'duration_commanded': 60}
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal1]

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("High congestion: No traffic signals required intervention or were suitable for autonomous GREEN phase change this cycle (considering cooldowns).")


@pytest.mark.asyncio
async def test_set_signal_phase_failure_logged_action_not_recorded(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}
    signal1 = SignalState(signal_id="sig_fail", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), location=LocationModel(latitude=1,longitude=1,name="SigFail"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal1]
    mock_traffic_signal_service.set_signal_phase.return_value = SignalControlCommandResponse(
        signal_id="sig_fail", status=SignalControlStatusEnum.FAILED, message="Controller error", timestamp=datetime.utcnow()
    )

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(signal_id="sig_fail", phase=SignalPhaseEnum.GREEN, duration_seconds=60)
    assert "sig_fail" not in agent_core._recent_signal_actions # Action failed, should not be in recent actions
    agent_core.logger.info.assert_any_call("Signal control response for sig_fail: Status='failed', Message='Controller error'")


@pytest.mark.asyncio
async def test_cooldown_cleanup(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"} # Trigger evaluations
    now = datetime.utcnow()

    # Signal that should remain in recent_actions (within cooldown)
    agent_core._recent_signal_actions["sig_active_cooldown"] = {
        'timestamp': now - timedelta(seconds=30),
        'phase_commanded': SignalPhaseEnum.GREEN,
        'duration_commanded': 60
    }
    # Signal whose cooldown should have expired
    expired_cooldown_timestamp = now - timedelta(seconds=agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS + 60)
    agent_core._recent_signal_actions["sig_expired_cooldown"] = {
        'timestamp': expired_cooldown_timestamp,
        'phase_commanded': SignalPhaseEnum.GREEN,
        'duration_commanded': 60
    }

    # Add a signal that might be controlled to ensure the loop runs
    controllable_signal = SignalState(signal_id="sig_control_target", current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=now, location=LocationModel(latitude=1,longitude=1,name="Target"))
    mock_traffic_signal_service.get_all_signal_states.return_value = [controllable_signal]


    # Act
    await agent_core.run_decision_cycle()

    # Assert
    assert "sig_active_cooldown" in agent_core._recent_signal_actions
    assert "sig_expired_cooldown" not in agent_core._recent_signal_actions
    agent_core.logger.info.assert_any_call("Removed signal sig_expired_cooldown from recent actions list (cooldown expired).")
    # Check if sig_control_target was controlled
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_control_target", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )

# Add a test for when only one signal is present and it's on cooldown
@pytest.mark.asyncio
async def test_high_congestion_one_signal_present_and_on_cooldown(agent_core, mock_traffic_signal_service, mock_analytics_service):
    # Arrange
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    signal_on_cooldown = SignalState(
        signal_id="sig_cooldown_single",
        current_phase=SignalPhaseEnum.RED, # Still RED, but action was attempted
        operational_status=SignalOperationalStatusEnum.ONLINE,
        last_updated=datetime.utcnow(),
        location=LocationModel(latitude=1,longitude=1,name="CooldownSignal")
    )
    mock_traffic_signal_service.get_all_signal_states.return_value = [signal_on_cooldown]

    recent_action_time = datetime.utcnow() - timedelta(seconds=45) # Within cooldown
    agent_core._recent_signal_actions[signal_on_cooldown.signal_id] = {
        'timestamp': recent_action_time,
        'phase_commanded': SignalPhaseEnum.GREEN,
        'duration_commanded': 60
    }

    # Act
    await agent_core.run_decision_cycle()

    # Assert
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call(f"Signal {signal_on_cooldown.signal_id} was recently acted upon. Skipping this cycle. Details: {{'timestamp': {recent_action_time}, 'phase_commanded': <SignalPhaseEnum.GREEN: 'GREEN'>, 'duration_commanded': 60}}")
    agent_core.logger.info.assert_any_call("High congestion: No traffic signals required intervention or were suitable for autonomous GREEN phase change this cycle (considering cooldowns).")
