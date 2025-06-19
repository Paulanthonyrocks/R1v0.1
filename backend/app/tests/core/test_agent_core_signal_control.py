import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call, mock_open
import json
import os
from uuid import UUID, uuid4

import pytest

from app.core.agent_core import AgentCore, GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS, ACTION_KPI_CONFIG, EFFECTIVENESS_MEMORY_FILEPATH # Import constants
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
    service.get_signal_post_action_kpis = AsyncMock(return_value={"flow_rate_absolute": 100})
    service.get_incident_response_post_action_kpis = AsyncMock(return_value={"clearance_time_seconds": 500})
    service.get_corridor_post_action_kpis = AsyncMock(return_value={"corridor_avg_travel_time_seconds": 90})
    return service

@pytest.fixture
def agent_core_with_patched_logger_and_persistence(mock_prediction_scheduler, mock_personalized_routing_service, mock_analytics_service, mock_traffic_signal_service):
    # This fixture will be used for tests that don't need to mock file operations specifically for load/save.
    # For specific load/save tests, os/json/open will be patched directly in the test.
    with patch('app.core.agent_core.logger', MagicMock(spec=logging.Logger)) as mock_logger:
        # Temporarily prevent actual file load during AgentCore init for most tests, unless testing load itself
        with patch('app.core.agent_core.AgentCore._load_effectiveness_memory', return_value={}) as mock_load_mem:
            core = AgentCore(mock_prediction_scheduler, mock_personalized_routing_service, mock_analytics_service, mock_traffic_signal_service)
            core.logger = mock_logger
            core.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
            core.action_effectiveness_config = ACTION_KPI_CONFIG # Assuming this was meant to be ACTION_EFFECTIVENESS_CONFIG
            core.action_performance_logs = []
            core.pending_kpi_collection = []
            core.action_effectiveness_memory = {} # Start fresh unless test overrides
            core.effectiveness_memory_filepath = "test_data/test_memory.json" # Use a test-specific path
            # Ensure test data directory exists if not already handled by _save_effectiveness_memory
            test_data_dir = os.path.dirname(core.effectiveness_memory_filepath)
            if not os.path.exists(test_data_dir):
                os.makedirs(test_data_dir, exist_ok=True)

            return core

# --- Test Cases for _load_effectiveness_memory ---

def test_load_memory_file_not_found(agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    with patch('os.path.exists', return_value=False) as mock_exists:
        loaded_mem = agent._load_effectiveness_memory()
        mock_exists.assert_called_once_with(agent.effectiveness_memory_filepath)
        assert loaded_mem == {}
        agent.logger.info.assert_any_call(f"Effectiveness memory file '{agent.effectiveness_memory_filepath}' not found. Starting fresh (normal on first run).")

@patch('json.load')
@patch('builtins.open', new_callable=mock_open, read_data='{"key": [0.5]}')
@patch('os.path.exists', return_value=True)
def test_load_memory_success(mock_exists, mock_file_open, mock_json_load, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    mock_json_load.return_value = {"action_type:target": [0.5, 0.8]}
    loaded_mem = agent._load_effectiveness_memory()
    assert loaded_mem == {"action_type:target": [0.5, 0.8]}
    agent.logger.info.assert_any_call(f"Loaded 1 entries from memory: {agent.effectiveness_memory_filepath}")

@patch('json.load', side_effect=json.JSONDecodeError("err", "doc", 0))
@patch('builtins.open', new_callable=mock_open, read_data="invalid json")
@patch('os.path.exists', return_value=True)
def test_load_memory_json_decode_error(mock_exists, mock_file_open, mock_json_load, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    loaded_mem = agent._load_effectiveness_memory()
    assert loaded_mem == {}
    agent.logger.error.assert_any_call(f"Error loading memory from '{agent.effectiveness_memory_filepath}': err: line 1 column 1 (char 0). Starting fresh.", exc_info=True)

@patch('json.load', return_value=[1,2,3]) # Not a dict
@patch('builtins.open', new_callable=mock_open)
@patch('os.path.exists', return_value=True)
def test_load_memory_invalid_data_structure_not_dict(mock_exists, mock_file_open, mock_json_load, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    loaded_mem = agent._load_effectiveness_memory()
    assert loaded_mem == {}
    agent.logger.warning.assert_any_call(f"Memory file '{agent.effectiveness_memory_filepath}' not a valid dict. Starting fresh.")

@patch('json.load', return_value={"key1": "not_a_list", "key2": [0.5, "mixed_type"]})
@patch('builtins.open', new_callable=mock_open)
@patch('os.path.exists', return_value=True)
def test_load_memory_invalid_data_bad_value_type(mock_exists, mock_file_open, mock_json_load, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    loaded_mem = agent._load_effectiveness_memory()
    assert loaded_mem == {} # Or should contain only valid parts if any
    agent.logger.warning.assert_any_call("Invalid data for key 'key1' in memory file '%s'. Skip.", agent.effectiveness_memory_filepath)
    agent.logger.warning.assert_any_call("Invalid data for key 'key2' in memory file '%s'. Skip.", agent.effectiveness_memory_filepath)


@patch('builtins.open', side_effect=IOError("File read error"))
@patch('os.path.exists', return_value=True)
def test_load_memory_io_error_on_open(mock_exists, mock_file_open, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    loaded_mem = agent._load_effectiveness_memory()
    assert loaded_mem == {}
    agent.logger.error.assert_any_call(f"Error loading memory from '{agent.effectiveness_memory_filepath}': File read error. Starting fresh.", exc_info=True)


# --- Test Cases for _save_effectiveness_memory ---

@patch('json.dump')
@patch('builtins.open', new_callable=mock_open)
@patch('os.makedirs')
@patch('os.path.exists', return_value=True) # Assume directory exists
@patch('os.path.dirname', return_value="test_data")
def test_save_memory_success_dir_exists(mock_dirname, mock_dir_exists, mock_makedirs, m_open, m_dump, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    agent.action_effectiveness_memory = {"key": [1.0]}
    result = agent._save_effectiveness_memory()
    assert result is True
    mock_dirname.assert_called_once_with(agent.effectiveness_memory_filepath)
    mock_dir_exists.assert_called_once_with("test_data")
    mock_makedirs.assert_not_called() # Dir already exists
    m_open.assert_called_once_with(agent.effectiveness_memory_filepath, 'w')
    m_dump.assert_called_once_with({"key": [1.0]}, m_open(), indent=4)
    agent.logger.info.assert_any_call(f"Successfully saved 1 memory entries to {agent.effectiveness_memory_filepath}")

@patch('json.dump')
@patch('builtins.open', new_callable=mock_open)
@patch('os.makedirs')
@patch('os.path.exists', return_value=False) # Dir does not exist
@patch('os.path.dirname', return_value="test_data_new")
def test_save_memory_success_creates_dir(mock_dirname, mock_dir_exists, mock_makedirs, m_open, m_dump, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    agent.action_effectiveness_memory = {"key": [1.0]}
    result = agent._save_effectiveness_memory()
    assert result is True
    mock_makedirs.assert_called_once_with("test_data_new", exist_ok=True)

@patch('os.makedirs', side_effect=OSError("Cannot create dir"))
@patch('os.path.exists', return_value=False)
@patch('os.path.dirname', return_value="test_data_fail")
def test_save_memory_dir_creation_os_error(mock_dirname, mock_dir_exists, mock_makedirs, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    result = agent._save_effectiveness_memory()
    assert result is False
    agent.logger.error.assert_any_call(f"Failed to create dir 'test_data_fail': Cannot create dir", exc_info=True)

@patch('builtins.open', side_effect=IOError("Cannot write"))
@patch('os.path.exists', return_value=True) # Assume dir exists
@patch('os.path.dirname')
def test_save_memory_io_error_on_open(mock_dirname, mock_dir_exists, mock_file_open, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    agent.action_effectiveness_memory = {"key": [1.0]}
    result = agent._save_effectiveness_memory()
    assert result is False
    agent.logger.error.assert_any_call(f"Error saving memory to '{agent.effectiveness_memory_filepath}': Cannot write", exc_info=True)

# --- Test Cases for Integration in run_decision_cycle ---

@patch('app.core.agent_core.AgentCore._save_effectiveness_memory', return_value=True) # Mock the save method itself
@pytest.mark.asyncio
async def test_run_cycle_calls_save_memory_when_updated(mock_save_method, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    agent._memory_updated_this_cycle = True # Simulate memory was updated

    await agent.run_decision_cycle()

    mock_save_method.assert_called_once()
    agent.logger.info.assert_any_call("Effectiveness memory was updated in this decision cycle. Attempting to save to file.")
    agent.logger.info.assert_any_call(f"Effectiveness memory successfully saved to {agent.effectiveness_memory_filepath}")


@patch('app.core.agent_core.AgentCore._save_effectiveness_memory')
@pytest.mark.asyncio
async def test_run_cycle_does_not_call_save_memory_when_not_updated(mock_save_method, agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    agent._memory_updated_this_cycle = False # Ensure memory was NOT updated

    await agent.run_decision_cycle()

    mock_save_method.assert_not_called()
    agent.logger.info.assert_any_call("Effectiveness memory was not updated in this decision cycle. No save operation performed.")

# (Other tests for adaptive logic, KPI scheduling, etc. assumed present)
