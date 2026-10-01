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
| **Evidence bundle assembly** | Collects incident record + snapshot stills into a single bundle with a JSON manifest | **Production.** SHA-256 sealed, verified 2026-09-30 |
| **Forensic search** | Attribute filtering over incident records (type, severity, feed, lane, time window, text) | **Partial.** See §2.2 |
| **Privacy masking** | Gaussian blur of caller-supplied face/plate boxes on export | **Production.** See §2.2 |
| **Release integrity** | The masked copy actually handed to a third party is sealed and separately verifiable | **Production.** Added 2026-10-01 |
| **Retention enforcement** | Age- and size-based cleanup of video, snapshots, pavement imagery, with 7-day default age cap | **Production** |
| **Audit trail** | SQLite audit_log table, indexed on timestamp, queried via `/logs` endpoint, 90-day retention | **Production** |

### 2.2 What is not yet production-ready — stated plainly

We would rather flag these now than have your technical team find them during due diligence. Two modules are partially implemented and each maps to a requirement in the Nigerian Data Protection Act conversation (§5):

**Forensic search scale.** The current search is a linear scan over incidents already resident in the database, bounded at 1,000 rows. It is adequate for a pilot corridor and will not hold at state volume across 36 states. Remediation: indexed query layer over incident attributes plus a plate-index table, specified in §7, Phase 2.

**Plate recognition (ANPR).** Detection of vehicles and of the region a plate occupies is in production and drives privacy masking. Reading the plate *characters* is not: there is no recogniser bundled, and the allow/block list feature is a string lookup that reports `unconfigured` until a recogniser is wired behind the same interface. Nothing in the evidence chain depends on it — a bundle is sealed and verifiable whether or not its plate was ever read — but any commitment to plate-level enforcement must wait for it.

**Two Phase 1 items closed since the first draft of this proposal**, recorded here so the change is visible rather than silent:

- *Evidence integrity* (previously flagged as our highest-priority gap) is closed. Every bundle carries a SHA-256 digest per artefact and a signed manifest; the 2026-09-30 live run sealed 10 of 10 bundles. See §2.3 for the exact scheme and its one honest limitation.
- *Privacy masking* is closed and was verified end-to-end, not merely enabled. Mask regions are derived automatically from live detector output and carried to the export step in a sidecar the detection worker writes; the caller no longer has to supply coordinates. With no vehicle in frame there are no regions to blur, and the release says `masked: false` with a reason instead of claiming a control that did not run.

We raise these because the credibility of this proposal depends on you being able to rely on our numbers. A vendor who hides these in a demo loses the account in month three.

### 2.3 Integrity scheme — precisely what we can and cannot prove

The seal is **HMAC-SHA256** over a canonicalised manifest, with a key held in a file on the appliance (`ROUTE_ONE_EVIDENCE_KEY_FILE`, created 0600). We state the scheme because a symmetric MAC is not a digital signature, and the distinction is material:

- **Proven:** the artefacts have not changed since sealing, and the manifest has not been altered since it was signed. Editing either — including editing the manifest *and* recomputing its digest — fails verification and names the specific artefact.
- **Not proven: non-repudiation.** Anyone holding the key can both seal and verify. Our verification responses return `non_repudiation: false` explicitly, so this can never be misread later as an asymmetry we do not have.
- Verification returns a report, never a bare boolean: manifest digest, signature, and each artefact separately, with expected versus actual digest.
- Sealing is fail-closed. If key material is unreachable, the bundle and the release are still written — an incident is never lost — but marked `sealed: false`, and verification of an unsealed document fails loudly rather than passing quietly.

**Both** the original bundle and the masked release copy are sealed. The release is the artefact a recipient actually holds, so it carries its own manifest, its own digests, and a pointer back to the digest of the bundle it derives from. Verification of the two is a separate endpoint, because a recipient holding the release needs the release verified.

The signer is an interface, not a function. An Ed25519 or KMS-held asymmetric key can replace the MAC without changing the manifest shape, the service, or the verification endpoint.

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

**Phase 1 — weeks 1–6. Integrity foundations. COMPLETE.**
Evidence bundle SHA-256 digest per artefact; signed manifest; verification endpoint for receiving counsel. Privacy masking on-by-default with automatic region derivation from the live detector output, carried to export by a sidecar written by the detection worker. Audit events extended to cover every evidence read, export, verification and release-verification, not only incident lifecycle. Retention defaults reviewed and confirmed against the state retention schedule (7-day media, 90-day audit).

Two additions made on review of the completed phase, both recorded in §2.3 rather than left implicit:

- **The masked release copy is sealed too.** The bundle's seal covers the unmasked originals, but the images a third party actually receives are different bytes produced by the masking pass. Releasing an unsealed copy would mean the artefact that leaves the building cannot be shown unaltered. The release now carries its own manifest and digests, a pointer back to the source bundle's digest, and its own verification endpoint.
- **The scheme is HMAC-SHA256, not a KMS-held asymmetric key.** An earlier draft of this plan specified KMS. What is deployed is a symmetric MAC over a canonicalised manifest, with the key in a 0600 file on the appliance. It proves integrity and authenticity; it does **not** provide non-repudiation, and every verification response says so. Moving to Ed25519 or KMS is a drop-in behind the existing signer interface and is scoped as a hardening item, not a Phase 1 remainder.

**Phase 2 — weeks 7–12. Forensic scale and multi-site.**
Indexed forensic query layer replacing the linear scan. Plate index table for cross-corridor search. Re-identification accuracy measurement and reporting, published to LASTMA monthly. On-premises appliance packaging and hardening for site deployment. Plate-character recognition (ANPR) behind the existing interface, so plate-level enforcement becomes possible; this does not gate the evidence chain.

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
