# Route One Traffic Hub — Capability Review
## Assessment for City Planning Operations

### Prepared for: City Planning Center
### Prepared by: Lead Developer, Route One Systems
### Date: November 2026 | Status: Final for leadership review
### Classification: Internal

---

## 1. Scope of this review

This review assesses the Route One Traffic Hub (R1v0.1) against the needs of city planning operations: what the platform can produce today, what it cannot, and where the platform's existing architecture could fill gaps that commercial traffic systems do not.

Every capability claim below is verified against the source repository at the state of this review. Where a module exists but is disabled, unwired, or mocked, that is stated explicitly — the distinction between "production" and "present in source" is the central finding of this review. Runtime behavior claims are backed by the platform's live-run history (documented in IMPROVEMENTS.md and run logs from September–October 2026). Section 7 states the full verification boundary.

---

## 2. Summary of findings

The Hub is a production-tuned **real-time operations** system whose vision pipeline — detection, tracking, cross-camera re-identification, wrong-way detection, hard-braking detection, incident workflow — is live, stable, and tuned against real runs.

It is **not yet a planning-grade data platform**, for three structural reasons that are all addressable:

1. **Nothing the planner needs is retained.** The platform persists raw detection tracks and hourly count-plus-speed aggregates. It does not persist per-class counts, per-lane counts, or turning movements — even though the per-frame equivalent is computed live in memory every frame.
2. **A whole tier of analytics exists in source but is disabled, unwired, or mocked.** A complete pedestrian/cyclist safety module is imported and never called. A complete pavement-condition package has zero callers. The signal controller is a mock URL. The prediction and anomaly models are gated off and their model files are not on disk.
3. **The measurements planning reports depend on are either absent or not defensible as-is.** Speed is estimated with a single flat pixels-per-meter scale for the entire depth of a scene (no per-lane calibration file exists). No turning-movement counts, no speed distributions, no delay estimates, and an emissions endpoint whose own code labels its method "heuristic."

None of this requires new hardware, new cameras, or a rewritten pipeline. The frames are already flowing through a system that computes more than it keeps.

---

## 3. Capabilities verified as production

### 3.1 Perception and measurement

| Capability | Basis | Status |
|---|---|---|
| Vehicle detection | YOLOv8n, TensorRT fp16 engine, imgsz 960, loaded on inference workers | **Production** — engine loaded and verified in live runs |
| Multi-object tracking | ByteTrack with appearance-weighted association, Kalman motion model | **Production** |
| Cross-camera re-identification | Redis-backed identity gallery; persistent across restarts | **Production** — gallery of 925 identities observed live |
| Lane assignment | Six positional lane bands, row-interpolated, with hysteresis | **Production** (CV lane model disabled; bands substitute — see 4.6) |
| Speed estimation | Pixel-displacement against a flat scale factor | **Production, with caveat** — see 4.7 |
| Plate recognition | OCR with confidence gating, plate-to-trajectory association | **Production** |
| Vehicle attributes | Car model and colour from classification | **Production** |

### 3.2 Traffic monitoring (per feed, computed every frame)

- Vehicle counts: instantaneous, cumulative, and **passage counts** (transit counting with a re-entry gap — the most methodologically sound counting in the platform, and the closest thing to a planning-grade count already present).
- Median speed; speeding and stopped vehicle counts; congestion score; per-lane counts; per-class counts (four classes); lane occupancy; queue lengths.
- **Calibration honesty**: where a camera is uncalibrated, speed is reported as unknown rather than fabricated, and the aggregates degrade cleanly. This is the correct design and it is rare.

### 3.3 Safety and incident analytics — the most mature part of the platform

- **Wrong-way detection**: learned per-lane flow vectors, two-way road suppression, multi-frame confirmation, teleport filtering. Tuned across several live runs; a 68-minute run produced zero false flags. This is genuinely production-grade.
- **Hard braking**: threshold at 12.0 m/s² with per-vehicle and per-feed cooldowns, and a written tuning history that distinguishes looping sample footage from genuine braking events — exactly the provenance discipline planning reports need.
- **Stopped vehicle and congestion incidents**, with snapshot capture rate-limited three ways and an evidence-integrity and audit-log layer beneath every incident.

### 3.4 Network analytics that function

- **Origin–destination matrix**: cross-feed re-identification transitions, computed as a vectorised crosstab. Correct, and honest about its limits (consecutive feed pairs only, one-hour travel cap).
- **Corridor travel times** between camera pairs.
- **Hourly history**: vehicle count and average speed per feed, up to 168 hours.

### 3.5 Operations infrastructure

- Multi-feed ingestion (up to 8 concurrent feeds), shared-memory frame transport, worker pool with watchdog and parent monitoring, graceful bounded shutdown.
- Incident workflow with evidence integrity, forensic service, audit logging.
- Camera health and calibration drift monitoring.
- Weather and event impact panels (external API integrations).
- Firebase authentication with role gates; a fail-closed tunnel auth gate.
- Retention policy enforced as configuration (7-day age cap, size cap) under the governing data-protection regime.

---

## 4. What is dead, stub, or disabled — the honest gap

### 4.1 Pedestrian and cyclist analysis: complete module, zero execution

The platform contains a full vulnerable-road-user module (near-miss by time-to-collision and distance, crosswalk dwell detection, per-class event construction). It is imported at the top of the inference worker and **never called**. Separately, the COCO classes for person (0) and bicycle (1) are excluded from the active detection class list, so pedestrians and cyclists are never even detected, let alone counted.

Consequence: no pedestrian volumes, no cyclist volumes, no exposure rates, no conflict-rate surrogates. Sidewalk, bike-lane, Vision-Zero and equity analyses are all blocked on this.

### 4.2 Traffic prediction: disabled, and the model file does not exist

The prediction scheduler is disabled by configuration. The predictor is lazy-loaded only when that flag is on. Even if enabled, the referenced model file (`traffic_predictor_model.h5`) is not present in the models directory. The networkx-based route optimizer in the same family runs on a demonstration 5×5 grid with hardcoded statistics. The advice surfaced in the UI is therefore rule-based fallback, not forecast.

### 4.3 Anomaly detection: disabled, model file absent

Same shape: flag off, `.h5` model path referenced but not on disk. The "anomaly detection" that runs is the heuristic safety layer from 3.3, which is fine — but the ML detector is not a capability tonight.

### 4.4 The /analyze endpoints: dead code that returns 500

Five analysis endpoints call into a module written against a MongoDB-style collection interface (`get_collection(...)`) that the database manager does not implement — MongoDB is disabled in configuration. Any client that touches these endpoints raises an unhandled exception and receives a 500. This is a defect, not merely an unreachable surface.

### 4.5 Signal controller: a mock

The signal service targets `http://localhost:8082/mock/signals`; the signals shown in the dashboard are seeded placeholders ("Main St & First Ave"). No phase timing, no actuation, no detector feed. Every downstream measure that requires signal state — control delay, level of service, split failure, progression — is blocked.

### 4.6 Lane model: positional bands, not a real lane model

The computer-vision lane detector is disabled. Positional bands substitute. Bands are adequate for wrong-way direction consensus; they are insufficient for turning movements, per-lane turning ratios, and per-lane speed distributions.

### 4.7 Speed calibration: one flat scale for the whole scene

The perspective-calibration file the config points at does not exist, and the speed code falls back to a single top-level pixels-per-meter constant for the entire frame depth. A vehicle at the near edge and a vehicle at the far edge are scaled identically. Near-lane speeds will read fast, far-lane slow. Median speeds on a center lane are plausible; per-class and per-lane speed statistics intended for a planning report are not defensible yet. The honest mitigation — reporting speed as unknown when a camera is uncalibrated — already exists; the fix is per-lane calibration, not new honesty.

### 4.8 Pavement analysis: finished, unplugged

A complete package exists: crack width measurement, pothole depth estimation, rutting analysis, a pavement condition index calculator, camera calibration for the pavement path, and a report generator. **No router, service, or caller references it.** It is a finished product with no plug. For planning, this is the single most valuable unconnected module in the repository.

### 4.9 Export and reporting

The export surface produces two datasets: incidents and feed statuses. There is no count matrix export, no per-class or per-lane series, no turning-movement report, no emissions anything — and no planner-native formats (15-minute TMAS-style count matrices, corridor summaries, GeoJSON tied to the street network).

### 4.10 What a planner can and cannot produce today

| Planning output | Today? | Blocking gap |
|---|---|---|
| Live counts and speeds per camera | Yes | — |
| Hourly volume + average speed history (≤7 days) | Yes | — |
| O-D matrix across camera pairs | Yes | — |
| Incident and alert history with evidence | Yes | — |
| Turning movement counts | **No** | No lane model, no movement classification |
| Class-specific volumes over time | **No** | Class counts never persisted |
| Speed distributions (85th percentile, pace) | **No** | Only median/mean; flat calibration |
| Volume/capacity, level of service | **No** | Not computed; signals are mock |
| Control delay at signals | **No** | Mock signal controller |
| Pedestrian / cyclist volumes | **No** | VRU module unexecuted; classes excluded |
| Signal retiming / before-after studies | **No** | No intervention tagging or comparison tooling |
| Planning-grade emissions | **No** | Endpoint is a self-labelled heuristic |
| Long-horizon statistics (seasonality, growth) | **No** | Retention prunes at 7 days; no rollups |
| Network-level rollups | **No** | Node congestion broadcast disabled |

---

## 5. Industry gaps Route One could uniquely fill

The commercial market (signal-adaptive and detector-centric vendors) sells cameras, sensors, adaptive signal control, and detection counts. What it does not sell a planning department is one camera-native platform where the same frames feed the operations room and the planning datamart, with per-feed provenance. Route One's architecture is unusually well placed for this. Five plays, in order of feasibility:

**5.1 Turning-movement counts from cameras that already exist.**
The industry still treats turning-movement counts as a manual study or a dedicated-camera product. Derivable here from re-identified trajectories plus a lane model — no new hardware, per-movement volumes by class and hour, with per-feed confidence attached. This is a real, unserved gap.

**5.2 One platform, two audiences — operations and planning from the same frame.**
The shortest path in the platform: persist per-class and per-lane counts (the live per-frame computation already exists) and expose rolling aggregates under the same provenance discipline already applied to safety tuning. That converts the Hub from an operations dashboard into the city's traffic datamart.

**5.3 Asset condition from the same video: the pavement module.**
A finished pavement-condition package sits beside a working traffic pipeline. PCI, pothole depth, cracking and rutting from the same feeds that produce counts is a combination no incumbent offers — operations vendors do not do pavement, and pavement vendors do not do counts. Wiring it is the single highest-value differentiator available.

**5.4 VRU safety surrogates at network scale.**
Near-miss, crosswalk dwell, wrong-way and hard-braking events computed continuously across the camera network give pedestrian/bike conflict rates per crosswalk — the direct input to Vision-Zero and equity analysis, from sensors already installed. Commercial safety analytics stops at vehicle events.

**5.5 Provenance-grade, auditable planning data.**
Per-feed calibration status, unknown-speed honesty, evidence integrity, audit logging, and retention policy as configuration are all already in the platform. Planning reports can therefore carry an accuracy and uncertainty statement per camera — the thing consultants bill for and legacy desks cannot provide.

---

## 6. Recommendations (ROI-ordered)

### Tier 1 — small code changes, high leverage, unblocks Tier 2

1. **Wire VRU detection and analysis.** Add COCO classes 0/1 to the detection class list; call the existing near-miss and crosswalk predicates in the inference loop. The module is already written. Unlocks pedestrian and cyclist volumes and their safety surrogates.
2. **Persist per-class and per-lane counts.** Add the per-frame class and lane breakdowns to the persisted metrics record (one write-site change). Every Tier 2 aggregation reads this.
3. **Fix or remove the dead /analyze endpoints.** The `get_collection` path raises an unhandled 500. Rewire to the active database or delete the surface; the current state fails any client that touches it.
4. **Wire the pavement-analysis package to an API.** Complete module, no callers. Needs a router and a report-generation endpoint.
5. **Add per-feed speed-calibration confidence to the metrics record.** Either produce per-lane calibration data, or gate published speed statistics with a confidence field so planning outputs carry accuracy. Gate now; calibrate next.

### Tier 2 — medium effort, creates the planning product

6. **Turning-movement counts.** Re-enable or improve the lane model and classify movements from existing tracked trajectories.
7. **Planning-grade export formats.** 15-minute count matrices (TMAS-style), per-class and per-lane series, corridor summaries, GeoJSON on the street network.
8. **Rolling aggregation service.** 15-minute, hourly, daily and monthly rollups retained beyond the 7-day raw window, with the raw prune unchanged.
9. **Speed distributions.** 85th percentile, pace, and compliance against posted limits, per class and lane, computed from stored tracks.

### Tier 3 — strategic; needs a partner decision or a model workstream

10. **Real signal-controller integration.** Replace the mock base URL with a driver interface (NTCIP-class), keeping the mock behind configuration. Unblocks delay, level of service, split failure, and progression.
11. **Honest prediction.** Either train and ship the missing model files (and confirm the framework is installable on the deployment box) or replace with a classical baseline that is labelled as classical. A disabled feature should not be shipped as a live one.
12. **A real emissions model.** Class-weighted, speed-variant factors replacing the placeholder multiplier at `routers/analysis.py`.
13. **Before/after study tooling.** Tag date ranges to an intervention, compare against a counterfactual window, emit a report. No vendor makes this easy; the history layer to support it is 80% present.

**Explicitly not recommended now:** the V2X UDP broadcast path (enabled but non-standard, with no consumer in the platform — retain unless a partner specifies SAE J2735), and route optimization (a demonstration grid — either build it on a real network graph or stop presenting it publicly).

---

## 7. Verification boundary

This review was conducted against the source repository without a live runtime; the production backend runs on the deployment box, not locally. Runtime behaviour claims (wrong-way tuning, hard-brake cooldowns, re-identification warm start, engine loading, zero-error runs) are supported by the platform's live-run record and logs. Structural claims were verified by tracing imports and callers across the codebase: the VRU module is imported and never invoked; the analysis endpoints call a database method that does not exist; the pavement package has no callers; the prediction and anomaly model files are absent from disk; the perspective-calibration file is absent from disk.

Where a claim could not be verified from source alone, it is not asserted as fact — the mock signal controller, for example, is verified as a mock by source inspection, but the behaviour of any real controller behind that interface is outside the scope of this review.

---

*End of review.*
