"""Regression probes for pipeline follow-up findings; no service startup."""
import ast
import importlib.util
import logging
import math
from pathlib import Path
import queue
import time
import typing
from types import SimpleNamespace

import numpy as np
import pytest

CORE = Path(__file__).resolve().parents[1] / 'app/core'


def core_method(name):
    tree = ast.parse((CORE / 'core_module.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CoreModule')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = dict(vars(typing), TrackData=dict, time=time, queue=queue, math=math,
              np=np, logger=logging.getLogger('test'))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'core_module.py', 'exec'), ns)
    return ns[name]


def test_vehicle_writer_does_not_queue_unhandled_feed_metrics():
    save = core_method('_save_vehicle_data')
    target = queue.Queue()
    core = SimpleNamespace(db_queue=target, feed_id='feed', _last_queue_warn_time=0,
                           _last_db_save_times={}, vehicle_type_map={2: 'car'})
    vehicles = {'car': {'bbox': [0, 0, 20, 20], 'centroid': [10, 10],
                        'class_id': 2, 'speed': None}}
    save(core, vehicles)
    messages = []
    while not target.empty():
        messages.append(target.get_nowait())
    assert [m['type'] for m in messages] == ['vehicle_data']
    assert messages[0]['speed'] is None


@pytest.mark.parametrize('new_class,new_embedding', [(2, [-1., 0.]), (7, [1., 0.])])
def test_rejected_detection_cannot_steal_existing_identity(new_class, new_embedding):
    spec = importlib.util.spec_from_file_location('followup_tracking', CORE / 'tracking.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tracker = module.TrackingManager({'tracking': {'probation_threshold': 2}}, fps=30)
    original = ((100, 100, 150, 150), 2, .9, np.array([1., 0.]))
    tracker.update([original], 1., (500, 500))
    tracker.update([original], 1.1, (500, 500))
    old_id = next(iter(tracker.vehicle_data))
    tracker.vehicle_data[old_id]['global_vehicle_id'] = 'original-global'
    incoming = (original[0], new_class, .9, np.array(new_embedding))
    tracks = tracker.update([incoming], 1.2, (500, 500))
    assert tracks[old_id]['status'] == 'predicting'
    assert tracks[old_id]['global_vehicle_id'] == 'original-global'
    assert len(tracks) == 2
    assert all(t.get('global_vehicle_id') != 'original-global'
               for tid, t in tracks.items() if tid != old_id)


def make_core(track, ground_positions=None):
    core = SimpleNamespace(
        config={}, confidence_threshold=.3, reid_embedder=None,
        tracker=SimpleNamespace(vehicle_data={'a': track}, update=lambda *args: {'a': track}),
        max_active_tracks=100, transformer=SimpleNamespace(is_calibrated=ground_positions is not None,
                                        pixel_to_ground=lambda pts: ground_positions),
        pixels_per_meter=30, _homography_fallback_warned=True, feed_id='feed',
        min_accel_dt_seconds=.01, min_speed_for_accel_kmh=5., min_physical_accel_mps2=-15.,
        ewma_alpha=.3, local_ocr=None, preprocessor=None, _lane_votes={},
        cached_lane_boundaries=[], last_detected_lane_lines=None,
        _assign_lane_band=lambda *args: None, _process_ocr_results=lambda *args: None,
        _save_vehicle_data=lambda *args: None)
    core._pixel_based_speed = lambda t: core_method("_pixel_based_speed")(core, t)
    return core


def finalize(core, timestamp):
    visible, _, _ = core_method('detect_and_track')(
        core, np.zeros((50, 50, 3)), 2, external_detections=[], timestamp=timestamp)
    return visible


@pytest.mark.parametrize('ground_positions', [None, [[10., 10.]]])
def test_pixel_velocity_is_not_reported_as_ground_speed(ground_positions):
    track = {'bbox': [10, 10, 30, 30], 'status': 'active', 'class_id': 2,
             'vx': 300., 'vy': 400., 'speed': 0., 'last_seen': 2.}
    core = make_core(track, ground_positions)
    assert finalize(core, 2.)['a']['speed'] is None


def test_detection_clears_stale_ground_state_when_calibration_unavailable():
    track = {'bbox': [10, 10, 30, 30], 'status': 'active', 'class_id': 2,
             'vx': 50., 'vy': 0., 'speed': 60., 'last_seen': 2.,
             'ground_coordinates': [15., 15.], 'prev_ground_pos': (10., 10.),
             'prev_t': 1., 'prev_speed_for_accel': 60., 'prev_speed_t': 1.,
             'acceleration': -5.}
    core = make_core(track)
    visible = finalize(core, 2.)
    assert visible['a']['speed'] is None
    assert visible['a']['acceleration'] == 0.
    assert 'ground_coordinates' not in track
    assert 'prev_ground_pos' not in track
    assert 'prev_t' not in track
    assert track['prev_speed_for_accel'] is None


def test_calibrated_speed_initializes_after_two_ground_samples():
    track = {'bbox': [10, 10, 30, 30], 'status': 'active', 'class_id': 2,
             'vx': 300., 'vy': 400., 'speed': None, 'last_seen': 2.}
    core = make_core(track, [[10., 10.]])
    assert finalize(core, 2.)['a']['speed'] is None
    core.transformer.pixel_to_ground = lambda pts: [[20., 10.]]
    assert finalize(core, 3.)['a']['speed'] == pytest.approx(36.)
    assert track['acceleration'] == 0.
    core.transformer.pixel_to_ground = lambda pts: [[30., 10.]]
    assert finalize(core, 4.)['a']['speed'] == pytest.approx(36.)

