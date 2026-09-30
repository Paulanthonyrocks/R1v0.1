# Route One Systems — Technical Proposal
## Enforcement Revenue Analytics Layer for LASTMA
### Prepared for: Lagos State Traffic Management Authority
### Date: September 2026 | Status: Draft for technical review
### Classification: Commercial in confidence

---

## 1. The problem we are proposing to solve

LASTMA has built real enforcement capability. In 2025 the authority recorded 113,000 technology-detected infractions, up from 55,000 in 2024 — a doubling that demonstrates the detection layer is working. The command centre, GPS patrol vehicles, body cameras and the central hub are all in place.

**The gap is between detection and collection.**

Lagos State publishes how many plates its cameras read. What has not been published — anywhere, in any period — is how many of those detections become paid fines. Across the state network, detection counts are reported and collection outcomes are not. The only formal examination of a Lagos public camera installation, at the Lekki toll plaza in October 2020, produced three official accounts that did not agree with one another: a judicial panel, a state white paper, and the federal government.

This is not a criticism of LASTMA. It is a structural consequence of building detection first. Detection produces a count. Collection requires evidence assembly, offender identification, notification, adjudication and reconciliation — five separate operational steps, each of which loses a percentage, and no one is measuring the loss.

**Our proposal is a single measurement and the machinery that acts on it:**

> Violations detected → notices issued → notices paid → naira collected, per camera, per corridor, per day.

---

## 2. What we are proposing

A software analytics layer that sits **on top of the camera infrastructure LASTMA and the Ministry already own and operate.** We propose no new cameras, no new control room, and no replacement of the existing Huawei ITS sites, ANPR network or TMS devices.

We ingest the video and metadata streams the state already produces, and we operate the analytics and evidence assembly that sits between a detected violation and money in a treasury account.

### 2.1 Core capability

| Capability | What it does | Current status |
|---|---|---|
| **Detection analytics** | YOLOv8 vehicle detection, ByteTrack multi-object tracking, cross-camera re-identification via Redis-backed identity matching | **Production.** Live in multi-feed operation |
| **Plate recognition** | OCR on detected vehicles, plate-to-trajectory association | **Production** |
| **Re-identification** | Tracks a vehicle across separate cameras, so a vehicle detected at one gantry is recognised at the next | **Production.** Warm-start from persisted identity state |
| **Speed and lane events** | Per-lane vehicle counts, speed estimation, lane-discipline events | **Production** |
| **ROI configuration** | Frontend-configurable regions of interest for targeted corridor and lane monitoring | **Production** |
| **Incident detection** | Accident, breakdown, obstruction and incident records with severity classification | **Production** |
| **Evidence bundle assembly** | Collects incident record + snapshot stills into a single bundle with a JSON manifest | **Partial.** See §2.2 |
| **Forensic search** | Attribute filtering over incident records (type, severity, feed, lane, time window, text) | **Partial.** See §2.2 |
| **Privacy masking** | Gaussian blur of caller-supplied face/plate boxes on export | **Partial.** See §2.2 |
| **Retention enforcement** | Age- and size-based cleanup of video, snapshots, pavement imagery, with 7-day default age cap | **Production** |
| **Audit trail** | SQLite audit_log table, indexed on timestamp, queried via `/logs` endpoint, 90-day retention | **Production** |

### 2.2 What is not yet production-ready — stated plainly

We would rather flag these now than have your technical team find them during due diligence. Three modules are partially implemented and each maps to a requirement in the Nigerian Data Protection Act conversation (§5):

**Evidence integrity.** The evidence bundle manifest currently records incident metadata, snapshot filenames and a bundle timestamp. **It contains no cryptographic hash of the bundle contents.** A manifest without a SHA-256 digest is an index, not a chain of custody. Under the Evidence Act and the ICT Act, a bundle that cannot be shown unaltered since capture is a bundle an offender's counsel will challenge. Remediation is specified in §7, Phase 1 — SHA-256 digest per artefact, manifest signed with a KMS-held key, verification endpoint for receiving counsel. This is the highest-priority engineering item in this proposal.

**Forensic search scale.** The current search is a linear scan over incidents already resident in the database, bounded at 1,000 rows. It is adequate for a pilot corridor and will not hold at state volume across 36 states. Remediation: indexed query layer over incident attributes plus a plate-index table, specified in §7, Phase 2.

**Privacy masking default.** Blur-on-export ships **disabled by default** and requires the caller to supply box coordinates from its own detector. No detector is bundled. For plate-bearing imagery this must be on-by-default with automated box derivation, specified in §7, Phase 1.

We raise these because the credibility of this proposal depends on you being able to rely on our numbers. A vendor who hides these in a demo loses the account in month three.

---

## 3. Why the enforcement-revenue layer, and not cameras

The Lagos State camera network is substantial. The Ministry reports 53 ANPR cameras logging 856,680 violations between January 2023 and March 2024, with 737,340 in the following review year. Huawei deployed four ITS sites in 2025. The network is bought, installed and running.

**That is the opportunity, not the obstacle.** A large installed base with no published collection outcome is a large installed base with unquantified, unaddressed revenue leakage. Every percentage point of detection-to-collection is direct revenue to the state, and today nobody can state what that percentage is.

We are not asking LASTMA to replace anything. We are asking to be measured against the hardware already in the ground.

---

## 4. The pilot we are proposing

### 4.1 Scope

**One corridor. Two to three existing ANPR/ITS camera sites. Ninety days.**

We propose the Alapere (80 km/h) and Nitel / Mobolaji Bank Anthony Way (60 km/h) checkpoint sites, or equivalent sites of LASTMA's choosing. These are existing deployments with published speed limits and known violation volumes, which makes them the cleanest possible measurement baseline.

### 4.2 Phase structure

**Phase 0 — Baseline (Days 1–15). We observe and measure only.**
No notices issued, no enforcement change, no operational interference. We pull the state's existing detection figures for the selected sites and establish what is currently collected versus detected. This baseline is the number the entire engagement is judged against, and it is the number the state has never published.

**Phase 1 — Evidence and notification integrity (Days 16–45).**
Deploy the evidence layer onto the pilot corridor. Every detection produces a signed, hash-verified bundle. We measure the drop-off at each step of LASTMA's existing workflow: detection → verification → notice issued → notice delivered → payment. **We do not change your workflow. We measure it.** Gaps found are reported to LASTMA with recommendations; adoption of those recommendations is LASTMA's decision.

**Phase 2 — Recovery optimisation (Days 46–90).**
Subject to Phase 1 findings and LASTMA's direction, we support prioritisation of the highest-value recovery actions, and provide the reconciliation report: detected, issued, paid, collected, outstanding, by site and by day.

### 4.3 Success criteria

Agreed with LASTMA before Phase 0 begins, and reported monthly:

| Metric | Baseline | Target |
|---|---|---|
| Detection-to-notice-issued ratio | **Measured in Phase 0** | Improvement agreed at Day 15 |
| Notice-to-payment ratio | **Measured in Phase 0** | Improvement agreed at Day 15 |
| Naira collected per camera per day | **Measured in Phase 0** | Improvement agreed at Day 15 |
| Evidence bundle integrity verification | Not currently performed | 100% of bundles hash-verified |
| Re-identification accuracy across pilot sites | Not currently measured | Measured and published |

We are not proposing headline percentage targets in advance of the Phase 0 baseline, because any number we invented today would be worthless to you and embarrassing to both of us in month two.

### 4.4 Commercial model

**No upfront capital expenditure.** No camera procurement. No new hardware beyond a single on-premises edge appliance per site (§5.2).

**Route One is remunerated as a percentage of naira actually collected above the Phase 0 baseline, attributable to the pilot corridor.** The state risks nothing and is paid nothing if collection does not improve.

This structure exists for a reason: it removes the budget-approval dependency that stalls public-sector technology pilots in Nigeria, and it aligns our incentive with yours exactly — we are paid on recovered revenue, not on licences, seats, or cameras.

---

## 5. Technical architecture

### 5.1 Current production stack

- **Frontend:** Next.js 16, React 19, TypeScript, Tailwind. Multi-feed surveillance matrix, ROI configuration, KPI dashboard, forensic search UI.
- **Backend:** Python 3.10+, FastAPI async API layer, 16 routers.
- **Detection:** YOLOv8 (Ultralytics), ONNX Runtime, OpenCV. ByteTrack multi-object tracking.
- **Re-identification:** Redis-backed identity store, cross-camera vehicle matching, warm-start from persisted state, pub/sub synchronisation.
- **Storage:** SQLite with lock-serialised batch writes and bounded retry. Retention enforcement at 7-day age cap by default.
- **Transport:** Binary WebSocket protocol for low-latency frame and telemetry broadcast, with per-client backpressure isolation and adaptive payload sizing by round-trip time.
- **Processing:** Distributed multiprocess inference pool, decoupled feed ingestion, horizontal scale-out with per-feed load balancing.

### 5.2 Deployment model — on-premises, data resident in Nigeria

**This is a hard requirement, not a preference.**

The system runs on-premise at the pilot site. Video, plate data, vehicle imagery and derived analytics remain on Nigerian soil. No personal data is transmitted to any jurisdiction outside Nigeria.

Proposed configuration per site: a single edge appliance running the full detection-to-evidence pipeline locally, sized to the site's feed count, with the operator console on the state's existing LAN. Where a site has an existing compute environment, Route One deploys as a containerised service on that environment instead.

Data residency is not a compliance checkbox for us. It is the reason this proposal is viable under the NDPA at all, and it is why we are an on-premises vendor rather than a SaaS platform in this market.

### 5.3 Integration

Inbound: RTSP video streams from existing ANPR/ITS/TMS infrastructure. Outbound: violation events and evidence manifests to LASTMA's existing systems.

Where LASTMA's TMS exposes an event interface, we write to it. Where it does not, we provide a reconciliation export so our figures reconcile against the state's own records — **we will always show our numbers alongside yours, and if they disagree we will say so first.**

---

## 6. Data protection and compliance

Route One is prepared to operate under the Nigeria Data Protection Act 2023 and NDPC regulation. A **Data Protection Impact Assessment and full annex is supplied as a separate document** (`NDPC_DATA_PROTECTION_ANNEX.md`) covering:

- Lawful basis and purpose limitation for each processing activity
- Data minimisation and the retention schedule
- Subject access, rectification and erasure handling
- Plate-data pseudonymisation and the default-masking control
- Processing register, data-subject register, and cross-border transfer position (nil — all processing on-premise)
- Data controller / data processor role allocation between LASTMA and Route One
- Breach notification procedure
- The FRSC–NDPC road-safety data governance alignment of October 2025, under which FRSC has committed to data minimisation, pseudonymisation and defined retention limits for enforcement data — a commitment we are aligned to and will match

Retention is enforced in code, not policy: age- and size-based cleanup with a 7-day default cap on video, snapshots and imagery, and a 90-day cap on audit records.

---

## 7. Delivery plan

**Phase 1 — weeks 1–6. Integrity foundations.**
Evidence bundle SHA-256 digest per artefact; signed manifest with KMS-held key; verification endpoint for receiving counsel. Privacy masking switched to on-by-default with automatic box derivation from the plate and person detectors. Audit events extended to cover every evidence read, export and verification, not only incident lifecycle. Retention defaults reviewed and confirmed against the state retention schedule.

**Phase 2 — weeks 7–12. Forensic scale and multi-site.**
Indexed forensic query layer replacing the linear scan. Plate index table for cross-corridor search. Re-identification accuracy measurement and reporting, published to LASTMA monthly. On-premises appliance packaging and hardening for site deployment.

**Phase 3 — weeks 13–20. Recovery and reporting.**
Reconciliation reporting: detected, issued, paid, collected, outstanding, by site and day. Dashboard for the command centre. Handover and operator training. Transition to a support agreement.

**Resourcing:** Route One provides the engineering team. LASTMA provides site access, camera stream endpoints, and a nominated technical counterpart. A joint steering group meets fortnightly.

---

## 8. Commercial summary

| Item | Position |
|---|---|
| Capital expenditure | Nil. No camera procurement. |
| Hardware | One on-premises edge appliance per site, at cost, refundable against fees |
| Professional services | Included in Phase 0 at no charge |
| Ongoing fees | Percentage of naira collected above Phase 0 baseline, pilot corridor only |
| Pilot term | 90 days from Phase 0 commencement |
| Exit | Either party may terminate on 30 days' written notice. No penalty, no minimum term |
| Data | Route One holds no data rights. All data belongs to the State. Full deletion certificate on exit |

**Exit on 30 days with no penalty is deliberate.** We are asking for a measurement, not a signature on an unfalsifiable promise.

---

## 9. Why Route One

1. **The pipeline is live, not a prototype.** Multi-feed detection, tracking, re-identification and plate recognition are in production operation. We are not asking you to evaluate a research system.
2. **We are not selling you cameras.** The state's network is large and functioning. Adding to it is not where the value is.
3. **We are paid on your revenue, not on your budget.** Our commercial success is arithmetically identical to yours.
4. **We told you what is broken.** Three modules are partial, and we named them before you found them.

---

## 10. Next steps

1. LASTMA technical review of this proposal; Route One technical deep-dive session with the state's engineering team.
2. Corridors and camera sites confirmed; Phase 0 baseline methodology agreed and signed off.
3. On-premises appliance specification and site survey.
4. Phase 0 commencement on written go-ahead.

---

**Route One Systems**
Lagos, Nigeria
Contact: [to be completed]
