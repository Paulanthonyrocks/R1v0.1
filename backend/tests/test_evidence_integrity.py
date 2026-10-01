"""Phase 1 evidence-integrity contract tests.

These assert the properties the proposal and NDPC annex actually promise, plus
the failure modes that matter most: a bundle must never verify when it has
been altered, and an unsealed bundle must never verify as if it were sealed.
"""
import importlib.util
import json
import logging
import os
import sys
import time
import types
import tempfile
from pathlib import Path
from unittest import TestCase, main

SERVICES = Path(__file__).resolve().parents[1] / 'app/services'
PACKAGE = 'evidence_integrity_services'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SERVICES)]
sys.modules[PACKAGE] = package

# evidence_service imports `from app.services import evidence_integrity`, which
# would execute the real app/services/__init__.py and drag in fastapi + the
# whole service registry. Alias app.services onto this stub package so the
# sibling import resolves locally and the test needs no ML/API dependencies.
app_pkg = types.ModuleType('app')
app_pkg.__path__ = []
sys.modules.setdefault('app', app_pkg)
sys.modules['app.services'] = package
setattr(app_pkg, 'services', package)


def load(name):
    spec = importlib.util.spec_from_file_location(f'{PACKAGE}.{name}', SERVICES / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


integrity = load('evidence_integrity')
evidence = load('evidence_service')
# privacy_service is loaded onto the same stub package and exposed as
# `evidence.privacy`, so a future `from app.services import privacy_service`
# inside evidence_service would resolve the same way.
privacy_mod = load('privacy_service')
setattr(evidence, 'privacy', privacy_mod)

# inference_worker imports cv2/numpy/the whole app at module scope, so it cannot
# be exec'd in this dependency-free harness. Extract ONLY the two sidecar
# helpers by source and run them against a stub module -- they touch no
# inference internals, and testing the real source (not a copy) is the point.
def load_sidecar_helpers():
    import ast
    src = (SERVICES.parent / 'core/inference_worker.py').read_text()
    tree = ast.parse(src)
    wanted = {'_plate_boxes_for_sidecar', '_write_mask_sidecar'}
    mod = types.ModuleType(f'{PACKAGE}.inference_worker')
    stub_globals = {
        '__name__': f'{PACKAGE}.inference_worker',
        'json': json, 'time': time, 'Path': Path,
        'logger': logging.getLogger('sidecar_test'),
        '_FRAME_DIMS_BY_FEED': {},
    }
    mod.__dict__.update(stub_globals)
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         '<inference_worker-extract>', 'exec'), mod.__dict__)
            found.add(node.name)
    missing = wanted - found
    assert not missing, f"sidecar helpers not found in inference_worker: {missing}"
    return mod

sidecar_mod = load_sidecar_helpers()
setattr(evidence, 'inference_worker', sidecar_mod)

KEY = b'phase1-test-key'
INCIDENT = {
    'incident_id': 'INC-0001',
    'type': 'ACCIDENT',
    'severity': 'HIGH',
    'source_feed_id': 'feed_alapere_01',
    'timestamp': 1750000000.0,
    'description': 'Collision at Alapere checkpoint',
}


def make_service(tmpdir, **cfg):
    conf = {'evidence': {'enabled': True, 'dir': tmpdir, 'seal': True, **cfg}}
    return evidence.EvidenceService(config=conf)


class HashingTests(TestCase):
    def test_sha256_file_returns_none_for_missing_file(self):
        # A missing artefact must never produce a digest that "verifies".
        self.assertIsNone(integrity.sha256_file(Path('/nonexistent/never.jpg')))

    def test_sha256_file_matches_known_digest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.bin'
            p.write_bytes(b'hello')
            self.assertEqual(
                integrity.sha256_file(p),
                integrity.sha256_bytes(b'hello'),
            )

    def test_canonical_json_is_key_order_independent(self):
        a = integrity.canonical_json({'x': 1, 'y': 2})
        b = integrity.canonical_json({'y': 2, 'x': 1})
        self.assertEqual(a, b, "digest must not depend on dict insertion order")


class SealTests(TestCase):
    def setUp(self):
        os.environ['ROUTE_ONE_EVIDENCE_KEY'] = KEY.decode()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY', None)
        self.tmp.cleanup()

    def _seed_snapshot(self):
        src = Path(self.dir) / 'snap.jpg'
        src.write_bytes(b'\xff\xd8\xff\xe0 fake jpeg bytes')
        return str(src)

    def test_bundle_is_sealed_and_verifies_clean(self):
        svc = make_service(self.dir)
        manifest = svc.save_bundle(INCIDENT, [self._seed_snapshot()], None)
        self.assertTrue(manifest['sealed'])
        self.assertIn('integrity', manifest)

        report = svc.verify_bundle('INC-0001')
        self.assertTrue(report['valid'], report.get('reason'))
        self.assertTrue(report['signature_valid'])
        self.assertTrue(report['manifest_digest_valid'])
        self.assertTrue(report['artefacts_valid'])

    def test_tampered_snapshot_fails_verification(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [self._seed_snapshot()], None)
        # Simulate post-seal alteration of the image.
        (Path(self.dir) / 'INC-0001' / 'snap.jpg').write_bytes(b'tampered')
        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['artefacts_valid'])
        failed = [a for a in report['artefacts'] if a.get('ok') is False]
        self.assertTrue(any(a['name'] == 'snap.jpg' for a in failed),
                        "the altered artefact must be named in the report")

    def test_edited_manifest_fails_digest_check(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [self._seed_snapshot()], None)
        mpath = Path(self.dir) / 'INC-0001' / 'manifest.json'
        m = json.loads(mpath.read_text())
        m['severity'] = 'LOW'  # downgrade severity after the fact
        mpath.write_text(json.dumps(m))

        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['manifest_digest_valid'])
        self.assertIn('modified after sealing', report['reason'])

    def test_forged_signature_is_rejected(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [self._seed_snapshot()], None)
        mpath = Path(self.dir) / 'INC-0001' / 'manifest.json'
        m = json.loads(mpath.read_text())
        # Attacker recomputes the digest after editing, but cannot sign it.
        m['severity'] = 'LOW'
        m['integrity']['manifest_digest'] = integrity.compute_manifest_digest(m)
        mpath.write_text(json.dumps(m))

        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['valid'])
        self.assertTrue(report['manifest_digest_valid'])
        self.assertFalse(report['signature_valid'],
                         "an edited manifest must not pass without a valid signature")

    def test_deleted_artefact_fails_verification(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [self._seed_snapshot()], None)
        (Path(self.dir) / 'INC-0001' / 'snap.jpg').unlink()
        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['valid'])

    def test_wrong_key_fails_verification(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [self._seed_snapshot()], None)
        os.environ['ROUTE_ONE_EVIDENCE_KEY'] = 'a-different-key'
        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['signature_valid'])


class FailClosedTests(TestCase):
    def setUp(self):
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY', None)
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY_FILE', None)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_unsealed_bundle_never_reports_valid(self):
        svc = make_service(self.dir)
        manifest = svc.save_bundle(INCIDENT, [], None)
        self.assertFalse(manifest['sealed'], "no key must not produce a sealed bundle")
        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['valid'])
        self.assertIn('never sealed', report['reason'])

    def test_missing_key_raises_signer_unavailable(self):
        with self.assertRaises(integrity.SignerUnavailable):
            integrity.get_signer()

    def test_missing_bundle_reports_not_found(self):
        svc = make_service(self.dir)
        report = svc.verify_bundle('does-not-exist')
        self.assertFalse(report['valid'])
        self.assertIn('no bundle', report['reason'])


class ManifestShapeTests(TestCase):
    def setUp(self):
        os.environ['ROUTE_ONE_EVIDENCE_KEY'] = KEY.decode()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY', None)
        self.tmp.cleanup()

    def test_snapshot_paths_are_rewritten_to_bundle_relative_names(self):
        src = Path(self.dir) / 'staging' / 'snap.jpg'
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b'jpeg')
        svc = make_service(self.dir)
        manifest = svc.save_bundle(INCIDENT, [str(src)], None)
        self.assertEqual(manifest['snapshots'], ['snap.jpg'])
        for name in manifest['snapshots']:
            self.assertFalse(Path(name).is_absolute(),
                             "absolute staging paths break re-hash after transfer")

    def test_clip_unavailable_is_recorded_honestly(self):
        svc = make_service(self.dir)
        manifest = svc.save_bundle(INCIDENT, [], None)
        self.assertTrue(manifest['clip_unavailable'])
        self.assertIsNone(manifest['clip'])

    def test_non_repudiation_is_reported_false_for_hmac(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [], None)
        report = svc.verify_bundle('INC-0001')
        self.assertFalse(report['non_repudiation'],
                         "HMAC is symmetric and must never be reported as "
                         "non-repudiation")

    def test_manifest_json_excluded_from_own_inventory(self):
        svc = make_service(self.dir)
        manifest = svc.save_bundle(INCIDENT, [], None)
        names = [a['name'] for a in manifest['artefacts']]
        self.assertNotIn('manifest.json', names)
        self.assertIn('incident.json', names)
        self.assertTrue(manifest['manifest_self_hash_excluded'])


class SnapshotCompletionTests(TestCase):
    """Bundles must actually acquire the snapshot that lands after minting."""

    def setUp(self):
        os.environ['ROUTE_ONE_EVIDENCE_KEY'] = KEY.decode()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.snapdir = Path(self.tmp.name) / 'snapshots'
        self.snapdir.mkdir()

    def tearDown(self):
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY', None)
        self.tmp.cleanup()

    def test_expected_names_cover_both_extensions(self):
        svc = make_service(self.dir)
        names = svc._expected_snapshot_names(INCIDENT)
        self.assertEqual(
            names,
            ['feed_alapere_01_INC-0001.jpg', 'feed_alapere_01_INC-0001.png'],
            "must match ingestion_worker's {feed_id}_{incident_id}{ext} naming",
        )

    def test_find_snapshots_on_disk_locates_worker_output(self):
        svc = make_service(self.dir)
        (self.snapdir / 'feed_alapere_01_INC-0001.jpg').write_bytes(b'worker frame')
        found = svc.find_snapshots_on_disk(INCIDENT, self.snapdir)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, 'feed_alapere_01_INC-0001.jpg')

    def test_missing_snapshot_dir_is_not_an_error(self):
        svc = make_service(self.dir)
        found = svc.find_snapshots_on_disk(INCIDENT, Path(self.dir) / 'nope')
        self.assertEqual(found, [])

    def test_late_snapshot_is_attached_and_resealed(self):
        svc = make_service(self.dir)
        minted = svc.save_bundle(INCIDENT, [], None)
        self.assertEqual(minted['snapshots'], [])
        # Original is sealed and valid while empty.
        self.assertTrue(svc.verify_bundle('INC-0001')['valid'])

        late = self.snapdir / 'feed_alapere_01_INC-0001.jpg'
        late.write_bytes(b'late arriving frame')
        found = svc.find_snapshots_on_disk(INCIDENT, self.snapdir)
        updated = svc.attach_snapshots(INCIDENT, [str(p) for p in found], None)

        self.assertEqual(updated['snapshots'], ['feed_alapere_01_INC-0001.jpg'])
        self.assertTrue(updated['sealed'])
        # Re-sealed, so it must still verify AFTER gaining an artefact.
        report = svc.verify_bundle('INC-0001')
        self.assertTrue(report['valid'], report.get('reason'))
        names = [a['name'] for a in report['artefacts']]
        self.assertIn('feed_alapere_01_INC-0001.jpg', names)

    def test_attach_to_missing_bundle_is_a_noop(self):
        svc = make_service(self.dir)
        self.assertIsNone(svc.attach_snapshots(INCIDENT, ['/nonexistent.jpg'], None))

    def test_reattach_same_snapshot_does_not_duplicate(self):
        svc = make_service(self.dir)
        svc.save_bundle(INCIDENT, [], None)
        p = self.snapdir / 'feed_alapere_01_INC-0001.jpg'
        p.write_bytes(b'frame')
        svc.attach_snapshots(INCIDENT, [str(p)], None)
        again = svc.attach_snapshots(INCIDENT, [str(p)], None)
        self.assertEqual(again['snapshots'], ['feed_alapere_01_INC-0001.jpg'])
        self.assertTrue(svc.verify_bundle('INC-0001')['valid'])


class PrivacyMaskingTests(TestCase):
    """Masking must never claim to have run when it did not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _svc(self, enabled=True):
        return evidence.privacy.PrivacyService(
            config={'privacy': {'enabled': enabled, 'blur_kernel': 31}}
        )

    def test_no_boxes_reports_unmasked_with_reason(self):
        src = self.dir / 'snap.jpg'
        src.write_bytes(b'not-a-real-jpeg')
        report = self._svc().mask_evidence_snapshot(src, self.dir / 'out.jpg')
        self.assertFalse(report['masked'])
        self.assertEqual(report['reason'], 'no face/plate boxes available to mask')
        self.assertFalse((self.dir / 'out.jpg').exists(),
                         "no output file when masking did not run")

    def test_disabled_privacy_does_not_mask(self):
        src = self.dir / 'snap.jpg'
        src.write_bytes(b'x')
        report = self._svc(enabled=False).mask_evidence_snapshot(
            src, self.dir / 'out.jpg', boxes=[(0, 0, 5, 5)])
        self.assertFalse(report['masked'])
        self.assertIn('disabled', report['reason'])

    def test_sidecar_boxes_are_parsed(self):
        svc = self._svc()
        src = self.dir / 'snap.jpg'
        src.write_bytes(b'x')
        (self.dir / 'snap.jpg.boxes.json').write_text(
            json.dumps({'boxes': [[1, 2, 3, 4], [5, 6, 7, 8]]}))
        boxes = svc._boxes_from_sidecar(src)
        self.assertEqual(boxes, [(1, 2, 3, 4), (5, 6, 7, 8)])

    def test_malformed_sidecar_yields_no_boxes(self):
        svc = self._svc()
        src = self.dir / 'snap.jpg'
        src.write_bytes(b'x')
        (self.dir / 'snap.jpg.boxes.json').write_text('{not json')
        self.assertEqual(svc._boxes_from_sidecar(src), [])

    def test_wrong_length_boxes_are_skipped(self):
        svc = self._svc()
        src = self.dir / 'snap.jpg'
        src.write_bytes(b'x')
        (self.dir / 'snap.jpg.boxes.json').write_text(
            json.dumps([[1, 2, 3], [1, 2, 3, 4, 5], [9, 9, 9, 9]]))
        self.assertEqual(svc._boxes_from_sidecar(src), [(9, 9, 9, 9)])


class SidecarDerivationTests(TestCase):
    """The sidecar is what makes masking actually run. Boxes must be real."""

    def _core(self, vehicle_data, feed_id='feed_1'):
        tracker = types.SimpleNamespace(vehicle_data=vehicle_data)
        return types.SimpleNamespace(tracker=tracker, feed_id=feed_id)

    def setUp(self):
        self.snapdir = Path(tempfile.mkdtemp())

    def test_plate_band_is_derived_from_vehicle_bbox(self):
        core = self._core({1: {'bbox': [100, 200, 300, 400], 'class_id': 2}})
        boxes = evidence.inference_worker._plate_boxes_for_sidecar(core, (640, 480))
        self.assertEqual(len(boxes), 1)
        x1, y1, x2, y2 = boxes[0]
        # Bottom-centre band inside the vehicle box.
        self.assertGreaterEqual(x1, 100)
        self.assertLessEqual(x2, 300)
        self.assertGreater(y1, 200)
        self.assertLessEqual(y2, 400)
        self.assertGreaterEqual(y1, 320, "band must start at ~60% down the vehicle")

    def test_pedestrians_are_excluded(self):
        core = self._core({1: {'bbox': [10, 10, 60, 200], 'class_id': 0}})  # person
        self.assertEqual(evidence.inference_worker._plate_boxes_for_sidecar(core, None), [])

    def test_no_core_or_no_tracks_returns_empty(self):
        self.assertEqual(evidence.inference_worker._plate_boxes_for_sidecar(None, None), [])
        self.assertEqual(
            evidence.inference_worker._plate_boxes_for_sidecar(self._core({}), None), [])

    def test_degenerate_bbox_is_skipped(self):
        core = self._core({
            'a': {'bbox': [100, 100, 100, 100], 'class_id': 2},   # zero area
            'b': {'bbox': None, 'class_id': 2},                    # missing
            'c': {'bbox': [50, 50, 150, 150], 'class_id': 2},     # valid
        })
        boxes = evidence.inference_worker._plate_boxes_for_sidecar(core, None)
        self.assertEqual(len(boxes), 1)

    def test_sidecar_written_when_boxes_exist(self):
        core = self._core({1: {'bbox': [100, 200, 300, 400], 'class_id': 2}})
        snap = self.snapdir / 'feed_1_INC-1.jpg'
        snap.write_bytes(b'x')
        ok = evidence.inference_worker._write_mask_sidecar(
            str(snap), core, {'privacy': {'enabled': True}})
        self.assertTrue(ok)
        sidecar = self.snapdir / 'feed_1_INC-1.jpg.boxes.json'
        self.assertTrue(sidecar.is_file())
        data = json.loads(sidecar.read_text())
        self.assertEqual(len(data['boxes']), 1)
        self.assertEqual(data['frame_alignment'], 'approximate')

    def test_no_sidecar_written_when_no_boxes(self):
        core = self._core({})
        snap = self.snapdir / 'feed_1_INC-2.jpg'
        snap.write_bytes(b'x')
        ok = evidence.inference_worker._write_mask_sidecar(
            str(snap), core, {'privacy': {'enabled': True}})
        self.assertFalse(ok)
        # An empty sidecar would read as "detector ran, found no plates".
        self.assertFalse((self.snapdir / 'feed_1_INC-2.jpg.boxes.json').exists())

    def test_privacy_disabled_writes_nothing(self):
        core = self._core({1: {'bbox': [100, 200, 300, 400], 'class_id': 2}})
        snap = self.snapdir / 'feed_1_INC-3.jpg'
        snap.write_bytes(b'x')
        ok = evidence.inference_worker._write_mask_sidecar(
            str(snap), core, {'privacy': {'enabled': False}})
        self.assertFalse(ok)
        self.assertFalse((self.snapdir / 'feed_1_INC-3.jpg.boxes.json').exists())

    def test_sidecar_is_consumed_by_masking(self):
        """End-to-end: worker sidecar -> PrivacyService finds boxes."""
        core = self._core({1: {'bbox': [100, 200, 300, 400], 'class_id': 2}})
        snap = self.snapdir / 'feed_1_INC-4.jpg'
        snap.write_bytes(b'x')
        evidence.inference_worker._write_mask_sidecar(
            str(snap), core, {'privacy': {'enabled': True}})
        priv = evidence.privacy.PrivacyService(
            config={'privacy': {'enabled': True, 'blur_kernel': 31}})
        boxes = priv._boxes_from_sidecar(snap)
        self.assertEqual(len(boxes), 1, "PrivacyService must read what the worker wrote")


class ReleaseSealTests(TestCase):
    """The masked RELEASE is the artefact a third party actually holds.

    The bundle's seal covers the unmasked originals; these assert the released
    copy carries its own chain of custody, and -- more importantly -- that it
    cannot be altered, or claimed as verified, after the fact.
    """

    def setUp(self):
        os.environ['ROUTE_ONE_EVIDENCE_KEY'] = KEY.decode()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY', None)
        self.tmp.cleanup()

    def _release(self, masked=2, total=None):
        """Build a bundle, then seal a release over N masked copies."""
        svc = make_service(self.dir)
        src = Path(self.dir) / 'snap.jpg'
        src.write_bytes(b'\xff\xd8\xff\xe0 fake jpeg bytes')
        bundle = svc.save_bundle(INCIDENT, [str(src)], None)

        release_dir = svc.release_path('INC-0001')
        release_dir.mkdir(parents=True, exist_ok=True)
        # `masked` images actually exist on disk; the remainder of `total`
        # were attempted and failed, so they are reported but never written.
        names = [f'rel_{i}.jpg' for i in range(masked)]
        for n in names:
            (release_dir / n).write_bytes(b'\xff\xd8 masked bytes')
        results = [{'name': n, 'masked': True, 'boxes': 1} for n in names]
        if total is not None and total > masked:
            results += [{'name': f'miss_{i}.jpg', 'masked': False,
                         'reason': 'no face/plate boxes available to mask'}
                        for i in range(total - masked)]
        release = svc.seal_release('INC-0001', release_dir, results,
                                   source_manifest=bundle)
        return svc, release_dir, release, bundle

    def test_release_is_sealed_and_verifies_clean(self):
        svc, release_dir, release, _ = self._release()
        self.assertTrue(release['sealed'])
        self.assertIn('integrity', release)
        report = svc.verify_release('INC-0001')
        self.assertTrue(report['valid'], report.get('reason'))
        self.assertTrue(report['signature_valid'])
        self.assertTrue(report['artefacts_valid'])
        self.assertEqual(report['document'], 'masked_release')

    def test_release_path_is_outside_the_bundle(self):
        svc, release_dir, _, _ = self._release()
        # Masking must never mutate the sealed originals.
        self.assertNotEqual(release_dir, svc.bundle_path('INC-0001'))
        self.assertFalse(release_dir.is_relative_to(svc.bundle_path('INC-0001')))

    def test_release_chains_to_sealed_source_bundle(self):
        svc, _, release, bundle = self._release()
        self.assertTrue(release['source_bundle_sealed'])
        self.assertEqual(
            release['derived_from_bundle_digest'],
            bundle['integrity']['manifest_digest'],
            "the released copy must name the digest of the original it derives from",
        )

    def test_tampered_release_image_fails_verification(self):
        svc, release_dir, _, _ = self._release()
        (release_dir / 'rel_0.jpg').write_bytes(b'swapped after sealing')
        report = svc.verify_release('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['artefacts_valid'])
        failed = [a for a in report['artefacts'] if a.get('ok') is False]
        self.assertTrue(any(a['name'] == 'rel_0.jpg' for a in failed),
                        "the altered release image must be named in the report")

    def test_edited_release_manifest_fails_signature(self):
        svc, release_dir, _, _ = self._release()
        mpath = release_dir / 'manifest.json'
        m = json.loads(mpath.read_text())
        # Claim a fuller masking than actually happened.
        m['privacy']['fully_masked'] = True
        m['privacy']['snapshots_masked'] = 99
        m['integrity']['manifest_digest'] = integrity.compute_manifest_digest(m)
        mpath.write_text(json.dumps(m))
        report = svc.verify_release('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['signature_valid'],
                         "an edited release manifest must not pass the signature check")

    def test_deleted_release_image_fails_verification(self):
        svc, release_dir, _, _ = self._release()
        (release_dir / 'rel_1.jpg').unlink()
        report = svc.verify_release('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['artefacts_valid'])

    def test_wrong_key_fails_release_verification(self):
        svc, release_dir, _, _ = self._release()
        os.environ['ROUTE_ONE_EVIDENCE_KEY'] = 'a-different-key'
        report = svc.verify_release('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['signature_valid'])

    def test_partial_masking_still_seals_and_says_so(self):
        """A partial release is a valid SEALED record of a partial masking.

        It must never be reported as fully_masked, and it must still verify --
        the seal attests what happened, not that masking was complete.
        """
        svc, _, release, _ = self._release(masked=1, total=2)
        self.assertTrue(release['sealed'])
        self.assertFalse(release['privacy']['fully_masked'])
        self.assertEqual(release['privacy']['snapshots_masked'], 1)
        self.assertEqual(release['privacy']['snapshots_total'], 2)
        report = svc.verify_release('INC-0001')
        self.assertTrue(report['valid'])
        self.assertFalse(report['privacy']['fully_masked'])

    def test_unsealed_release_never_verifies(self):
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY', None)
        os.environ.pop('ROUTE_ONE_EVIDENCE_KEY_FILE', None)
        svc = make_service(self.dir)
        src = Path(self.dir) / 'snap.jpg'
        src.write_bytes(b'bytes')
        bundle = svc.save_bundle(INCIDENT, [str(src)], None)
        release_dir = svc.release_path('INC-0001')
        release_dir.mkdir(parents=True, exist_ok=True)
        (release_dir / 'rel_0.jpg').write_bytes(b'masked')
        release = svc.seal_release('INC-0001', release_dir,
                                   [{'name': 'rel_0.jpg', 'masked': True}],
                                   source_manifest=bundle)
        self.assertFalse(release['sealed'], "no key must fail closed")
        self.assertTrue((release_dir / 'rel_0.jpg').exists(),
                        "the artefact must still be produced, never lost")
        report = svc.verify_release('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['sealed'])

    def test_verify_release_without_a_release_says_so(self):
        svc = make_service(self.dir)
        report = svc.verify_release('INC-0001')
        self.assertFalse(report['valid'])
        self.assertFalse(report['sealed'])
        self.assertIn('no release', report['reason'])


if __name__ == '__main__':
    main()
