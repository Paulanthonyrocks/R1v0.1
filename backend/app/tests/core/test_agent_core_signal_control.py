import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call, mock_open
import json
import os
from uuid import UUID, uuid4

import pytest

from app.core.agent_core import AgentCore, GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS, ACTION_KPI_CONFIG, ACTION_EFFECTIVENESS_CONFIG
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
    return SignalState(signal_id=signal_id, current_phase=current_phase, operational_status=SignalOperationalStatusEnum.ONLINE,
                       location=LocationModel(latitude=1.0, longitude=1.0, name=signal_id), last_updated=datetime.utcnow(), main_flow_direction="NS")

@pytest.fixture
def mock_analytics_service():
    service = MagicMock(spec=AnalyticsService)
    default_kpis = {"overall_congestion_level": "LOW"}
    for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: default_kpis[kpi_name] = "LOW"
    service.get_current_system_kpis_summary = MagicMock(return_value=default_kpis)
    service.get_critical_alert_summary = AsyncMock(return_value={"critical_unack_alert_count": 0, "recent_critical_types": [], "active_alerts": []})

    # Ensure all KPI collection methods are AsyncMocks for call assertions
    service.get_signal_current_kpis = AsyncMock(return_value={"current_flow_vph": 100})
    service.get_corridor_current_kpis = AsyncMock(return_value={"avg_travel_time_seconds": 150})
    service.get_incident_area_current_kpis = AsyncMock(return_value={"avg_speed_kmh": 20})

    service.get_signal_post_action_kpis = AsyncMock(return_value={"flow_rate_absolute": 100, "local_congestion_level": "LOW"})
    service.get_incident_response_post_action_kpis = AsyncMock(return_value={"clearance_time_seconds": 500, "local_congestion_level_incident_zone": "LOW", "avg_speed_kmh_incident_zone": 30})
    service.get_corridor_post_action_kpis = AsyncMock(return_value={"corridor_avg_travel_time_seconds": 110, "corridor_throughput_vph": 750})

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
            core.action_effectiveness_config = ACTION_EFFECTIVENESS_CONFIG
            core.action_performance_logs = []
            core.pending_kpi_collection = []
            core.action_effectiveness_memory = {}
            core.effectiveness_memory_filepath = "test_data/test_memory.json"
            test_data_dir = os.path.dirname(core.effectiveness_memory_filepath)
            if not os.path.exists(test_data_dir): os.makedirs(test_data_dir, exist_ok=True)
            return core

# --- Existing Test Cases ... (Assumed present) ...

# --- Test Cases for _calculate_effectiveness_score (Focusing on Green Wave and Congestion) ---

def test_calculate_effectiveness_score_green_wave_scenarios(agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    action_type = "GREEN_WAVE_ACTIVATION"
    corridor_id = "main_st_ns_wave" # Uses baselines: TT=100s, TP=800vph

    # Scenario 1: Good Performance
    log_data_good = {
        "action_type": action_type, "pre_action_context_kpis": {"corridor_id": corridor_id, "expected_demand_level": "HIGH", "avg_travel_time_seconds": 120, "throughput_vph": 700}, # Pre-action data
        "post_action_kpis": {"corridor_avg_travel_time_seconds": 70, "corridor_throughput_vph": 900} # Post-action data
    }
    score_good, metrics_good = agent._calculate_effectiveness_score(log_data_good)
    assert score_good is not None and score_good > 0.5 # Expect high positive: (0.5 from TT) + (0.5 from TP) = 1.0
    assert metrics_good == {"gw_corridor_id": corridor_id, "gw_pre_demand_level": "HIGH", "pre_gw_avg_travel_time": 120, "pre_gw_throughput": 700, "gw_post_avg_travel_time": 70, "gw_post_throughput": 900}

    # Scenario 2: Poor Performance
    log_data_poor = {
        "action_type": action_type, "pre_action_context_kpis": {"corridor_id": corridor_id, "avg_travel_time_seconds": 90, "throughput_vph": 800},
        "post_action_kpis": {"corridor_avg_travel_time_seconds": 180, "corridor_throughput_vph": 300}
    }
    score_poor, metrics_poor = agent._calculate_effectiveness_score(log_data_poor)
    assert score_poor is not None and score_poor < -0.5 # Expect high negative: (-0.5 from TT) + (-0.4 from TP) = -0.9
    assert "pre_gw_avg_travel_time" in metrics_poor # Check some pre-KPIs are extracted

    # Scenario 3: Missing Post KPIs
    log_data_missing_post = {"action_type": action_type, "pre_action_context_kpis": {"corridor_id": corridor_id}, "post_action_kpis": {}}
    score_missing, _ = agent._calculate_effectiveness_score(log_data_missing_post)
    assert score_missing is None # Scoring function returns None if essential post KPIs are missing

    # Scenario 4: Missing Pre-Action corridor_id (context for baselines)
    log_data_missing_pre_context = {"action_type": action_type, "pre_action_context_kpis": {"expected_demand_level": "HIGH"}, "post_action_kpis": {"corridor_avg_travel_time_seconds": 70, "corridor_throughput_vph": 900}}
    score_missing_pre, metrics_missing_pre = agent._calculate_effectiveness_score(log_data_missing_pre_context)
    # Score might still be calculated using default baselines if corridor_id is missing but post KPIs are good
    assert "gw_corridor_id" not in metrics_missing_pre
    assert score_missing_pre > 0.5 # Uses default baselines, should still be good score

def test_calculate_effectiveness_score_congestion_relief_scenarios(agent_core_with_patched_logger_and_persistence):
    agent = agent_core_with_patched_logger_and_persistence
    action_type = "SET_SIGNAL_GREEN_CONGESTION"

    # Scenario 1: Good: High congestion -> Low, with specific pre-action signal details
    log_data_good = {
        "action_type": action_type,
        "pre_action_context_kpis": {"overall_congestion": "HIGH", "signal_initial_phase": "RED", "current_flow_vph": 100, "queue_lengths_meters": {"N":50}},
        "post_action_kpis": {"local_congestion_level": "LOW", "flow_rate_absolute": 800}
    }
    score_good, metrics_good = agent._calculate_effectiveness_score(log_data_good)
    assert score_good is not None and score_good >= 1.0 # HIGH -> LOW is +1.0
    assert metrics_good.get("pre_overall_congestion_proxy") == "HIGH"
    assert metrics_good.get("post_local_congestion") == "LOW"
    assert "pre_flow" not in metrics_good # Not in config for this action type
    assert "pre_queue_N" not in metrics_good # Not in config for this action type

    # Scenario 2: Bad: Medium congestion -> High
    log_data_bad = {
        "action_type": action_type,
        "pre_action_context_kpis": {"overall_congestion": "MEDIUM", "signal_initial_phase": "RED"},
        "post_action_kpis": {"local_congestion_level": "HIGH", "flow_rate_absolute": 100}
    }
    score_bad, _ = agent._calculate_effectiveness_score(log_data_bad)
    assert score_bad is not None and score_bad <= -0.5 # MEDIUM -> HIGH is -0.5

    # Scenario 3: Missing post_local_congestion
    log_data_missing = {
        "action_type": action_type,
        "pre_action_context_kpis": {"overall_congestion": "HIGH"},
        "post_action_kpis": {"flow_rate_absolute": 500} # Missing local_congestion_level
    }
    score_missing, _ = agent._calculate_effectiveness_score(log_data_missing)
    assert score_missing == 0.0 # Should default to neutral if essential post_local_congestion is missing

# --- Review and Confirm Existing Adaptive Green Wave Selection Tests ---
# These tests should still function as they test the selection mechanism.
# The avg_score calculation within run_decision_cycle now uses the refined scoring,
# so the memory setup in these tests is still valid for influencing selection.

@patch('app.core.agent_core.datetime', new_callable=MagicMock)
@pytest.mark.asyncio
async def test_green_wave_priority_selection_uses_avg_score_tie_breaker(mock_dt, agent_core_with_patched_logger_and_persistence, mock_analytics_service, mock_traffic_signal_service):
    # This test was from previous step and should still work if memory setup is correct
    # and _execute_green_wave is mocked correctly.
    agent_core = agent_core_with_patched_logger_and_persistence
    now = datetime(2023, 1, 1, 7, 30, 0); mock_dt.utcnow.return_value = now
    main_st_config = GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]
    alt_st_config = GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"] # P1, different signals

    kpis = {"overall_congestion_level": "LOW"}
    kpis[main_st_config["demand_kpi_trigger"]] = "LOW" # P1 main_st time-triggered
    kpis[alt_st_config["demand_kpi_trigger"]] = "HIGH" # P1 alt_st demand-triggered
    mock_analytics_service.get_current_system_kpis_summary.return_value = kpis

    agent_core.action_effectiveness_memory[f"GREEN_WAVE_ACTIVATION:main_st_ns_wave"] = [0.4]
    agent_core.action_effectiveness_memory[f"GREEN_WAVE_ACTIVATION:alt_st_ew_wave"] = [0.8]

    all_sids = main_st_config["signals_in_order"] + alt_st_config["signals_in_order"]
    mock_signal_states = [create_candidate_signal(sid) for sid in all_sids]
    mock_traffic_signal_service.get_all_signal_states.return_value = mock_signal_states
    agent_core._execute_green_wave = AsyncMock(return_value=True)

    await agent_core.run_decision_cycle()

    # Both are P1. alt_st_ew_wave has better score (0.8 vs 0.4)
    agent_core.logger.info.assert_any_call("Sorted candidate corridors by priority: ['alt_st_ew_wave' (Prio: 1, AvgScore: 0.80), 'main_st_ns_wave' (Prio: 1, AvgScore: 0.40)].")
    agent_core.logger.info.assert_any_call("Activating selected green wave: 'alt_st_ew_wave'.")
    agent_core._execute_green_wave.assert_called_once_with(corridor_id="alt_st_ew_wave", config=ANY, signals_in_order=ANY, green_phase=ANY, green_time_seconds=ANY, offsets_seconds=ANY, all_current_signal_states=ANY, processed_signals_for_coordination=ANY, now_utc=ANY)

# (Other existing tests like detailed sequence, skipping offline etc. are assumed present)

# Ensure ACTION_EFFECTIVENESS_CONFIG is accessible (it's imported)
# The agent_core fixture now sets self.action_effectiveness_config = ACTION_EFFECTIVENESS_CONFIG
# so the scoring methods use the version from app.core.agent_core module.

# --- Test Cases for Epsilon-Greedy General Congestion Logic ---

@pytest.mark.asyncio
async def test_congestion_logic_explores_when_epsilon_triggered(agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    agent_core.exploration_epsilon = 0.1
    # Ensure overall_congestion_level is HIGH to trigger the logic
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_a_state = create_candidate_signal("sig_A", SignalPhaseEnum.RED)
    sig_b_state = create_candidate_signal("sig_B", SignalPhaseEnum.RED)
    sig_c_state = create_candidate_signal("sig_C", SignalPhaseEnum.RED)

    agent_core.action_effectiveness_memory = {
        "SET_SIGNAL_GREEN_CONGESTION:sig_A": [0.2], # Low score
        "SET_SIGNAL_GREEN_CONGESTION:sig_B": [0.8], # High score (would be chosen by exploit)
        "SET_SIGNAL_GREEN_CONGESTION:sig_C": [0.5]  # Mid score
    }
    all_mock_states = [sig_a_state, sig_b_state, sig_c_state]
    mock_traffic_signal_service.get_all_signal_states.return_value = all_mock_states

    # Candidate dict entry for sig_A (the one we force rng.choice to pick)
    # Note: The actual list passed to rng.choice will contain all candidates.
    # We are mocking rng.choice to return a specific one from that list.
    # The structure of dict entry must match what's created in AgentCore.
    candidate_a_dict_entry = {
        'signal_id': 'sig_A',
        'signal_state': sig_a_state,
        'avg_score': 0.2
    }

    with patch.object(agent_core.rng, 'random', return_value=0.05) as mock_rng_random, \
         patch.object(agent_core.rng, 'choice', return_value=candidate_a_dict_entry) as mock_rng_choice:

        await agent_core.run_decision_cycle()

    mock_rng_random.assert_called_once()
    # Assert that rng.choice was called. The argument to choice will be a list of dicts.
    # We need to ensure it was called with a list of the correct candidates.
    # The order within candidate_signals_for_congestion_relief before choice is not guaranteed if scores are calculated on the fly.
    # So, check that the *set* of signal_ids in the choice list is correct.
    assert mock_rng_choice.call_count == 1
    args_list, _ = mock_rng_choice.call_args
    passed_candidates_list = args_list[0]
    assert len(passed_candidates_list) == 3
    assert {cand['signal_id'] for cand in passed_candidates_list} == {'sig_A', 'sig_B', 'sig_C'}

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_A", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    agent_core.logger.info.assert_any_call(
        "EXPLORATORY_RANDOM general congestion action: Randomly selected signal 'sig_A' from 3 candidates. (Its avg score: 0.20)"
    )

    assert len(agent_core.pending_kpi_collection) == 1
    kpi_entry = agent_core.pending_kpi_collection[0]
    assert kpi_entry['action_parameters']['selection_method'] == "EXPLORATORY_RANDOM"
    assert kpi_entry['pre_action_context_kpis']['chosen_candidate_avg_score'] == 0.2
    assert kpi_entry['pre_action_context_kpis']['num_candidates_considered'] == 3
    assert kpi_entry['target_ids'] == ["sig_A"]


@pytest.mark.asyncio
async def test_congestion_logic_exploits_best_when_epsilon_not_triggered(agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    agent_core.exploration_epsilon = 0.1
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_a_state = create_candidate_signal("sig_A", SignalPhaseEnum.RED) # score 0.2
    sig_b_state = create_candidate_signal("sig_B", SignalPhaseEnum.RED) # score 0.8 (best)
    sig_c_state = create_candidate_signal("sig_C", SignalPhaseEnum.RED) # score 0.5

    agent_core.action_effectiveness_memory = {
        "SET_SIGNAL_GREEN_CONGESTION:sig_A": [0.2],
        "SET_SIGNAL_GREEN_CONGESTION:sig_B": [0.8],
        "SET_SIGNAL_GREEN_CONGESTION:sig_C": [0.5]
    }
    all_mock_states = [sig_a_state, sig_b_state, sig_c_state] # Order here doesn't matter for exploitation after sorting
    mock_traffic_signal_service.get_all_signal_states.return_value = all_mock_states

    with patch.object(agent_core.rng, 'random', return_value=0.5) as mock_rng_random, \
         patch.object(agent_core.rng, 'choice') as mock_rng_choice: # Should not be called

        await agent_core.run_decision_cycle()

    mock_rng_random.assert_called_once()
    mock_rng_choice.assert_not_called()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_B", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    agent_core.logger.info.assert_any_call(
        "EXPLOITATIVE_BEST_SCORE general congestion action: Selected signal 'sig_B' (Avg score: 0.80). Top candidates considered (ID, Score): [{'id': 'sig_B', 'score': '0.80'}, {'id': 'sig_C', 'score': '0.50'}, {'id': 'sig_A', 'score': '0.20'}]"
    )

    assert len(agent_core.pending_kpi_collection) == 1
    kpi_entry = agent_core.pending_kpi_collection[0]
    assert kpi_entry['action_parameters']['selection_method'] == "EXPLOITATIVE_BEST_SCORE"
    assert kpi_entry['pre_action_context_kpis']['chosen_candidate_avg_score'] == 0.8
    assert kpi_entry['target_ids'] == ["sig_B"]

@pytest.mark.asyncio
async def test_congestion_logic_handles_single_candidate_exploration(agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    agent_core.exploration_epsilon = 0.1
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_x_state = create_candidate_signal("sig_X", SignalPhaseEnum.RED)
    agent_core.action_effectiveness_memory = {"SET_SIGNAL_GREEN_CONGESTION:sig_X": [0.3]}
    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_x_state]

    candidate_x_dict_entry = {'signal_id': 'sig_X', 'signal_state': sig_x_state, 'avg_score': 0.3}

    with patch.object(agent_core.rng, 'random', return_value=0.05) as mock_rng_random, \
         patch.object(agent_core.rng, 'choice', return_value=candidate_x_dict_entry) as mock_rng_choice:
        await agent_core.run_decision_cycle()

    mock_rng_random.assert_called_once()
    mock_rng_choice.assert_called_once_with([candidate_x_dict_entry]) # Called with a list containing the single candidate dict

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_X", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    agent_core.logger.info.assert_any_call(
        "EXPLORATORY_RANDOM general congestion action: Randomly selected signal 'sig_X' from 1 candidates. (Its avg score: 0.30)"
    )
    assert len(agent_core.pending_kpi_collection) == 1
    kpi_entry = agent_core.pending_kpi_collection[0]
    assert kpi_entry['action_parameters']['selection_method'] == "EXPLORATORY_RANDOM"
    assert kpi_entry['pre_action_context_kpis']['chosen_candidate_avg_score'] == 0.3

@pytest.mark.asyncio
async def test_congestion_logic_handles_single_candidate_exploitation(agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    agent_core.exploration_epsilon = 0.1
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_x_state = create_candidate_signal("sig_X", SignalPhaseEnum.RED)
    agent_core.action_effectiveness_memory = {"SET_SIGNAL_GREEN_CONGESTION:sig_X": [0.7]}
    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_x_state]

    with patch.object(agent_core.rng, 'random', return_value=0.5) as mock_rng_random, \
         patch.object(agent_core.rng, 'choice') as mock_rng_choice: # Should not be called
        await agent_core.run_decision_cycle()

    mock_rng_random.assert_called_once()
    mock_rng_choice.assert_not_called()

    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_X", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    agent_core.logger.info.assert_any_call(
         "EXPLOITATIVE_BEST_SCORE general congestion action: Selected signal 'sig_X' (Avg score: 0.70). Top candidates considered (ID, Score): [{'id': 'sig_X', 'score': '0.70'}]"
    )
    assert len(agent_core.pending_kpi_collection) == 1
    kpi_entry = agent_core.pending_kpi_collection[0]
    assert kpi_entry['action_parameters']['selection_method'] == "EXPLOITATIVE_BEST_SCORE"
    assert kpi_entry['pre_action_context_kpis']['chosen_candidate_avg_score'] == 0.7

@pytest.mark.asyncio
async def test_congestion_logic_no_candidates_no_action(agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    # Scenario 1: No signals returned
    mock_traffic_signal_service.get_all_signal_states.return_value = []
    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("General Congestion: No suitable signals found for congestion relief this cycle after filtering.")
    assert len(agent_core.pending_kpi_collection) == 0

    # Scenario 2: Signals are not eligible (e.g., all GREEN or OFFLINE)
    mock_traffic_signal_service.reset_mock() # Reset call counts for set_signal_phase
    agent_core.logger.reset_mock()
    agent_core.pending_kpi_collection = []

    all_green_signal = create_candidate_signal("sig_green", SignalPhaseEnum.GREEN)
    offline_signal = create_candidate_signal("sig_offline", SignalPhaseEnum.RED)
    offline_signal.operational_status = SignalOperationalStatusEnum.OFFLINE
    mock_traffic_signal_service.get_all_signal_states.return_value = [all_green_signal, offline_signal]

    await agent_core.run_decision_cycle()
    mock_traffic_signal_service.set_signal_phase.assert_not_called()
    agent_core.logger.info.assert_any_call("General Congestion: No suitable signals found for congestion relief this cycle after filtering.")
    agent_core.logger.debug.assert_any_call("Signal sig_green skipped (already GREEN).")
    agent_core.logger.debug.assert_any_call("Signal sig_offline skipped (not ONLINE).")
    assert len(agent_core.pending_kpi_collection) == 0

@pytest.mark.asyncio
async def test_congestion_logic_respects_cooldown(agent_core_with_patched_logger_and_persistence, mock_traffic_signal_service, mock_analytics_service):
    agent_core = agent_core_with_patched_logger_and_persistence
    agent_core.exploration_epsilon = 0.0 # Force exploitation for predictability
    mock_analytics_service.get_current_system_kpis_summary.return_value = {"overall_congestion_level": "HIGH"}

    sig_a_state = create_candidate_signal("sig_A", SignalPhaseEnum.RED)
    sig_b_state = create_candidate_signal("sig_B", SignalPhaseEnum.RED) # Will be put on cooldown

    agent_core.action_effectiveness_memory = {
        "SET_SIGNAL_GREEN_CONGESTION:sig_A": [0.7], # Best non-cooldown
        "SET_SIGNAL_GREEN_CONGESTION:sig_B": [0.9], # Best overall, but on cooldown
    }
    # Simulate sig_B was actioned recently
    agent_core._recent_signal_actions["sig_B"] = {
        'timestamp': datetime.utcnow() - timedelta(seconds=agent_core.SIGNAL_ACTION_COOLDOWN_SECONDS / 2),
        'reason': 'some_other_reason'
    }
    mock_traffic_signal_service.get_all_signal_states.return_value = [sig_a_state, sig_b_state]

    await agent_core.run_decision_cycle()

    # sig_B should be skipped due to cooldown, so sig_A is chosen
    mock_traffic_signal_service.set_signal_phase.assert_called_once_with(
        signal_id="sig_A", phase=SignalPhaseEnum.GREEN, duration_seconds=60
    )
    agent_core.logger.debug.assert_any_call("Signal sig_B skipped (on cooldown). Last action: some_other_reason at %s." % agent_core._recent_signal_actions["sig_B"]['timestamp'])
    agent_core.logger.info.assert_any_call(
        "EXPLOITATIVE_BEST_SCORE general congestion action: Selected signal 'sig_A' (Avg score: 0.70). Top candidates considered (ID, Score): [{'id': 'sig_A', 'score': '0.70'}]"
    )
    assert len(agent_core.pending_kpi_collection) == 1
    kpi_entry = agent_core.pending_kpi_collection[0]
    assert kpi_entry['target_ids'] == ["sig_A"]
