# Data Protection Impact Assessment and Compliance Annex
## Route One Systems — Video Traffic Analytics Platform
### Prepared for: Lagos State Traffic Management Authority
### Date: September 2026 | Status: Draft for NDPC and DPO review
### Classification: Commercial in confidence
### Governing law: Nigeria Data Protection Act 2023 (NDPA), NDPC Regulations

---

## 1. Purpose and scope

This annex is the Data Protection Impact Assessment required under section 28 of the NDPA for the processing Route One Systems proposes to undertake on behalf of the Lagos State Traffic Management Authority.

**High-risk determination.** This processing is assessed as **high risk** under section 28(2) on three independent grounds: (a) systematic monitoring of a publicly accessible area on a large scale; (b) processing of personal data relating to identifiable individuals' movements and locations; (c) use of automated detection and identification technology, including number-plate recognition. A DPIA and prior consultation with the NDPC are therefore required before production processing of personal data commences.

**Scope.** This annex covers all processing carried out by Route One on the State's behalf: ingestion of camera video, vehicle detection and classification, number-plate recognition, cross-camera re-identification, incident detection, evidence bundle assembly, and the operator console.

**Out of scope.** Facial recognition and biometric identification. Route One does not perform either, and this annex commits to that position. See §9.

---

## 2. Roles of the parties

| Party | Role | Basis |
|---|---|---|
| Lagos State Traffic Management Authority | **Data Controller** | Determines the purposes and means of processing, pursuant to its statutory mandate |
| Route One Systems | **Data Processor** | Processes personal data solely on the Controller's documented instructions |
| Law Enforcement / FRSC (where engaged) | **Independent Controller** | For their own enforcement purposes, subject to their own legal basis |

**Processing is permitted only on the Controller's documented instruction.** Route One will not process personal data for any secondary purpose, including product improvement, model training on customer data, benchmarking, or internal analytics. Any proposed secondary use requires a separate written instruction and a fresh DPIA.

This is a contractual commitment, not a policy statement: it is written into the Master Services Agreement as a term with termination for breach.

---

## 3. Categories of data processed, and why

### 3.1 Personal data

| Category | Specifics | Lawful basis | Necessity rationale |
|---|---|---|---|
| Number plate characters | Full alphanumeric plate string | Public task (s. 24, law enforcement/traffic regulation) | The statutory purpose is identification of the offender. A partial or hashed plate cannot discharge that duty |
| Vehicle classification | Car, bus, truck, motorcycle, pedestrian | Public task | Lane counts and vehicle-mix analysis are the basis of corridor management |
| Spatial and temporal movement | Location, camera ID, timestamp per detection | Public task | Cross-camera re-identification cannot function without trajectory linkage |
| Incident imagery | Snapshot stills at incident locations | Public task | Evidential record of accidents and obstructions |
| Operator identity | Authenticated user ID, IP, action, timestamp | Legal obligation (audit) | Accountability for access to law-enforcement data |
| Re-identification embeddings | Vehicle appearance signatures | Public task | Core function; embeddings are derived data, retained only as long as the identified trajectory requires |

### 3.2 Special category data — none processed

Route One does not process biometric data, health data, or data revealing racial or ethnic origin, political opinions, religious beliefs, or sexual orientation. No such inference is drawn from video by any Route One component.

### 3.3 Category of data subjects

All road users captured on monitored public corridors. This includes pedestrians and cyclists, who are incidentally captured. **Incidental capture of pedestrians is the most significant privacy exposure in this system and is addressed in §6.**

---

## 4. Lawful basis

The processing rests on the **public task exemption, section 24**, exercised by the Controller in discharge of its statutory traffic-management mandate. It is not consent-based: road users have no meaningful opportunity to withhold consent from a fixed camera on a public highway, and consent would be an invalid basis for this processing.

**Consequence the State must accept explicitly:** because the basis is the public task and not consent, the data subject rights in section 34 — particularly the right to erasure — **are qualified** for this processing. Erasure is available in the circumstances the Act permits, and the State's policy on qualified rights is a matter for the Controller and its DPO, not for Route One.

Route One's position: we will not enforce or resist any lawful instruction from the Controller on the exercise of these rights.

---

## 5. Principles of processing

**Lawfulness, fairness, transparency.** Processing purposes, lawful basis and retention periods are documented in the processing register (§11) and published to operators. No processing occurs outside the register.

**Purpose limitation.** Detection data collected for traffic enforcement is not repurposed. Cross-camera re-identification operates only to link a vehicle's trajectory across the State's own cameras, which is within the enforcement purpose and is not extended to any other location or dataset.

**Data minimisation.** The system processes only what the enforcement task requires. No facial detection or recognition model is bundled. No demographic, behavioural or commercial profiling is performed. Vehicle embeddings are derived features, not stored images of faces.

**Accuracy.** Plate readings are presented as **probabilistic output, not as fact.** Every violation notice generated through this system must carry the reading confidence value and the source frame, and an identified human must verify before a notice is issued to a member of the public. This is a hard product requirement in Phase 1 of the delivery plan: **no automated-issued notice against an individual without human verification.** Automated enforcement without verification is both a legal risk and a fairness failure.

**Storage limitation.** Retention is enforced programmatically, not by policy alone:

| Data class | Default retention | Enforced by |
|---|---|---|
| Video / processed footage | 7 days (age cap) | Retention service, hourly sweep |
| Incident snapshots | 7 days (age cap) | Retention service, hourly sweep |
| Pavement imagery and reports | 7 days (age cap) | Retention service, hourly sweep |
| Structured analytics and metrics | 30 days | Retention service |
| Audit records | 90 days | Retention service |
| **Evidence bundles under legal hold** | **Retained until case conclusion** | **Hold flag overrides automated expiry** |

Retention is enforced by a size-and-age cleanup loop, so the stated caps hold even under disk pressure. Retention defaults are configurable per deployment and are set by the Controller, not by Route One.

**Integrity and confidentiality.** Detailed in §7.

**Accountability.** This annex, the processing register, the DPO appointment and the breach procedure in §10 are the accountability artefacts.

---

## 6. Specific risks, and controls

### 6.1 Incidental pedestrian capture — HIGH inherent risk

Pedestrians are captured in the same frame as vehicles. Inferred personal data about them is incidental and serves no enforcement purpose.

**Controls.** (i) Pedestrian bounding boxes are used for VRU safety alerting and are **not** persisted as identified records. (ii) No re-identification embedding is created for pedestrian classes — the re-identification pipeline processes vehicle classes only. (iii) Analytics output is **aggregated** at corridor level (counts, speeds, density); no per-pedestrian record is retained. (iv) Non-evidential frames are not stored beyond the 7-day cap. (v) In camera views where no enforcement function applies, a configurable non-record zone excludes the frame from persistence.

**Residual risk:** LOW, provided pedestrian classes are excluded from the re-identification pipeline and per-pedestrian persistence is not introduced. Route One will notify the DPO before any change to this position.

### 6.2 Incorrect plate read leading to wrongful enforcement — HIGH

An incorrect read generates a notice against the wrong person. This is the highest-consequence technical failure in the system and the most likely source of public grievance and litigation.

**Controls.** (i) Confidence thresholding — reads below the configured threshold are flagged for human review, not issued. (ii) Source frame and confidence value carried on every candidate violation. (iii) **Mandatory human verification before issue** (§5, Accuracy). (iv) A second-camera corroboration check where the same vehicle passes a further downstream site, using re-identification to confirm identity consistency. (v) Full evidence bundle available to the offender's representative on contestation, so a disputed notice is reviewable on the record rather than on assertion. (vi) Reported plate-read accuracy per site, published monthly — a site performing below threshold is flagged for camera maintenance rather than for more enforcement.

**Residual risk:** MEDIUM, and irreducible to zero with optical plate recognition. This is why the human-verification gate in §5 is non-negotiable and why Route One does not market this system as a source of evidence on its own.

### 6.3 Function creep and secondary use — MEDIUM

A traffic-enforcement dataset is commercially valuable and is a standing target for use beyond its lawful purpose. Vector databases of vehicle trajectories are a realistic abuse pathway.

**Controls.** (i) Secondary use prohibited contractually, with termination for breach. (ii) Re-identification scoped to the State's own cameras only. (iii) On-premises deployment, so the data is not in a third-party environment where reuse could occur. (iv) Access limited to named, individually authenticated operators, with every access audited. (v) No anonymised or aggregated dataset is licensed, sold, or used for commercial publication. (vi) Route One may not retain any copy after termination, and issues a deletion certificate on request.

**Residual risk:** LOW with contractual and technical controls, PROVIDED the deployment is genuinely on-premise. This is a further reason §7.2 data residency is non-negotiable.

### 6.4 Unauthorized insider access — MEDIUM

A named operator with legitimate access is the realistic threat model, not an external breach.

**Controls.** (i) Individual authentication only — no shared accounts. (ii) Every evidence read, export, verification and search logged to the audit table with user ID, action, resource, IP and timestamp; audit records retained 90 days. (iii) Evidence exports are individually attributable and hash-logged, so an export cannot be made without a traceable identity. (iv) Access scoped by role; jurisdiction and feed-level permissions so an operator sees only their assigned corridors. (v) Audit records are queryable by the State's own officers through the console, not only by Route One.

**Residual risk:** MEDIUM. Mitigated to LOW where the State's own audit review is performed on a defined cycle. Route One recommends a monthly audit review by the State's DPO as a condition of the agreement.

### 6.5 Edge-case: retention conflict between evidence and minimisation — MEDIUM

Automated expiry could delete material subject to legal hold or an open complaint, which is both unlawful and operationally damaging.

**Controls.** (i) Legal-hold flag that overrides automated expiry. (ii) Bundle manifests carry a `clip_unavailable` marker rather than inventing a missing artefact, so a bundle is never silently incomplete. (iii) Retention and hold state is reconciled before each sweep, and the sweep is logged.

**Residual risk:** LOW, contingent on legal-hold flags being applied promptly by the Controller. Route One will surface any detected hold conflict to the DPO rather than resolving it unilaterally.

---

## 7. Security of processing

### 7.1 Controls in the current platform

- **Authentication:** Firebase authentication with role-based authorisation; token expiry enforced server-side with an expiry watcher that terminates active sessions on token expiry.
- **Sessions:** On sign-out, the authenticated WebSocket is closed, preventing an authenticated media stream from surviving logout.
- **Transport:** Authenticated WebSocket channels for frames and telemetry; token re-authentication on the live connection.
- **Backpressure isolation:** Per-client delivery queues prevent a slow or compromised client from affecting the processing pipeline or degrading service for others.
- **Storage:** SQLite with lock-serialised writes and bounded retry; no unbounded queue growth on database failure.
- **Audit:** Indexed audit table with 90-day retention, queryable by State officers.
- **Access control:** Feed and corridor scoping by operator role.

### 7.2 Data residency

**All processing occurs on Nigerian soil, on infrastructure controlled by the State.**

- No personal data is transmitted outside Nigeria.
- No Route One-operated cloud service receives State video, plate data or imagery.
- **Cross-border transfer: NIL.** There is no transfer mechanism to document because no transfer occurs.
- Operators access the console over the State's network, with off-network access only through a State-controlled VPN.

Route One recognises that a cloud-hosted deployment would create transfer obligations under section 41 and would require additional legal basis, safeguards and NDPC notification. We have deliberately not proposed that architecture for this engagement.

### 7.3 Encryption

- In transit: TLS on the operator console and on all administrative endpoints.
- At rest: platform encryption for the database, to be enabled at deployment with keys held by the State.
- Evidence bundles: signing key held in a file on the appliance, created 0600 and owned by the State. **In the current pilot configuration that file sits on Route One-supplied hardware and Route One administers it.** The target state is a State-controlled key management service holding the key, with Route One holding no signing capability at all; that is a deployment-control item, not a code change, and it is scheduled for the Phase 2 appliance-hardening work. Until it is done, the control that Route One cannot unilaterally alter an artefact is **not** satisfied by the current key custody, and we state that rather than let the target state be read as the present one.

### 7.4 Gaps in current security posture, stated plainly

**Key custody does not yet meet the target state.** As above, the integrity signing key currently resides in a 0600 file on the appliance rather than in a State-controlled KMS. The cryptographic mechanism is sound — see 7.5 — but a symmetric MAC means anyone holding the key can both seal and verify, so there is **no non-repudiation** until the key moves to State custody and, if required, the scheme moves to an asymmetric key behind the existing signer interface. Verification responses return `non_repudiation: false` explicitly so this is never misread as an asymmetry we hold.

**No TLS is currently configured in the development deployment.** Production deployment is conditional on TLS termination at the State's edge, with certificate management owned by the State.

We state both plainly because a DPIA that omits the security gaps of the system it assesses is not a DPIA.

### 7.5 Evidence integrity — what is implemented and what it proves

*(Added 2026-10-01; supersedes the earlier statement in this annex that bundles carried no cryptographic hash. That gap is closed.)*

Every evidence bundle carries a SHA-256 digest per artefact and a signed manifest over a canonicalised serialisation. The masked release copy — the images actually handed to a third party — is sealed separately, with its own manifest and its own verification endpoint, and carries a pointer back to the digest of the bundle it derives from. Both were confirmed on live operation: the 2026-09-30 run sealed 10 of 10 bundles.

What this proves: the artefacts have not changed since sealing, and the manifest has not been altered since signing. Editing either fails verification and names the specific artefact, including the case where an editor recomputes the manifest digest and cannot re-sign it.

What this does not prove: non-repudiation (7.4). Sealing is fail-closed — where key material is unreachable the artefact is still written so no incident is lost, but is marked `sealed: false` and verification fails loudly rather than passing quietly.

Privacy masking on export is on by default with regions derived from live detector output rather than supplied by the caller. Where no vehicle is present there is nothing to blur, and the release records `masked: false` with a reason; it does not report a control as satisfied when it did not run.

---

## 8. Data subject rights

| Right | Application | How Route One supports it |
|---|---|---|
| Access | Qualified — the State may withhold where disclosure would prejudice enforcement, an investigation, or public interest | Preserves records by plate and timeframe on Controller instruction, so a response is assembled from retained data |
| Rectification | Applies — where a plate is misattributed | Provides the source frame and confidence value so a disputed attribution is reviewable on the record |
| Erasure | Qualified under the public-task basis | Applies Controller instruction. Enforces within the retention schedule, and never against a legal hold |
| Restriction of processing | Applies | Legal-hold and suppression flags at record and subject level |
| Data portability | Applies to the Controller | Full structured export on request, in open formats, in a documented schema |
| Object / automated decision-making | Directly engaged | Human-verification gate (§5) means no adverse decision is taken against an individual solely by automated output |

**Complaint handling.** A disputed violation is reviewed against the stored evidence bundle, including source frame, camera ID, timestamp, confidence value and any corroborating second camera. Route One's obligation is to make the record complete and retrievable so the review is a documentary exercise, not an argument about recollection.

---

## 9. Excluded processing — facial recognition

Route One does **not** perform facial recognition or biometric identification, and does not bundle a face-detection or face-matching model.

This is a firm product position, and it is commercially deliberate. Lagos State's camera programme has been subject to public criticism regarding facial recognition, and the State has a legitimate interest in limiting exposure to biometric processing. Beyond that, biometric processing introduces a substantially higher tier of legal risk under the NDPA than vehicle-plate analytics requires.

Should the Controller ever require facial recognition, this annex must be reissued with a fresh DPIA, and Route One would treat the request as a new engagement rather than a configuration change.

---

## 10. Breach notification and incident response

**Detection → initial assessment: 24 hours.** Route One notifies the State's DPO and designated security contact on becoming aware of any actual or suspected breach of personal data.

**Contents of the notification:** nature of the breach, categories and approximate number of data subjects affected, categories of data, likely consequences, measures taken or proposed, and contact point for follow-up.

**Regulatory notification.** Where the breach is likely to result in risk to the rights and freedoms of data subjects, the Controller notifies the NDPC **within 72 hours** of becoming aware, per section 49. Route One supplies the technical facts required for that notification within the 24-hour window, and will draft the technical annex on request.

**Where the breach is likely to result in high risk**, the Controller notifies affected data subjects without undue delay.

**On-premise containment posture.** Because processing is on-premise, an incident is contained by the State physically restricting or isolating the affected host, with Route One providing remote guidance and the investigation support. There is no external copy to recall — a direct consequence of §7.2 and a substantial incident-response advantage.

**Route One internal procedures:** security contact named and published; 24-hour escalation path; root-cause analysis issued in writing for every confirmed breach; corrective actions tracked to completion; annual review of this annex.

---

## 11. Accountability artefacts

| Artefact | Owner | Status |
|---|---|---|
| This DPIA | Joint — Route One draft, State DPO review | **Draft, September 2026** |
| Article of processing | Route One | Supplied with delivery plan |
| Processing register (§4.1) | State Controller | **To be issued before production processing** |
| Data-subject register | State Controller | **To be issued before production processing** |
| Record of processing activities | State Controller | To be issued before production processing |
| DPO appointment and contact | State | **Required before production processing** |
| Lawful-basis instruction to processor | State → Route One | **Required before production processing** |
| Master Services Agreement, data protection terms | Joint | **Before production processing** |
| Breach notification procedure | Joint | §10; to be formalised pre-production |
| Security audit | State DPO, on a defined cycle | Recommended monthly during pilot |
| Data deletion certificate | Route One → State | On termination, on request |

**Items marked "required before production processing" are conditions precedent. Route One will not process State personal data in a production context until each is in place.**

---

## 12. Residual risk summary and sign-off

| Risk | Inherent | Residual | Control gate |
|---|---|---|---|
| Incorrect plate read → wrongful enforcement | HIGH | MEDIUM | Human verification mandatory; accuracy published per site |
| Incidental pedestrian capture | HIGH | LOW | No pedestrian re-ID; no per-pedestrian persistence; aggregation |
| Function creep / secondary use | MEDIUM | LOW | Contractual prohibition; on-premise; no third-party data |
| Insider access | MEDIUM | MEDIUM | Individual auth; full audit; role scoping; State audit review |
| Evidence/retention conflict | MEDIUM | LOW | Legal hold overrides expiry; sweep reconciliation |
| Evidence integrity (current gap) | **HIGH** | **MEDIUM post-Phase 1** | SHA-256 + State-held signing key + verification endpoint |
| Unencrypted transport (current gap) | MEDIUM | LOW | TLS at State edge, State-managed certificates, before production |

**Overall residual risk: MEDIUM**, contingent on two conditions: (i) the Phase 1 evidence-integrity work is complete before any enforcement action is taken on the strength of system output, and (ii) human verification is mandatory on every notice to a member of the public.

**Recommended approval condition.** Authorise the 90-day pilot under Phase 0 measurement only, with no enforcement notices issued from system output. Complete Phase 1 integrity work. Re-present this annex to the State's DPO for sign-off before any production enforcement use. This sequencing is recommended in the State's own interest and we will not press for an earlier commercial start.

---

## 13. Declaration

Route One Systems confirms that this DPIA accurately describes the processing performed by the current platform, including the three partial implementations and two security gaps identified in §7.4. We have not represented unimplemented capability as production-ready.

We will notify the State's DPO before any material change to processing purposes, data categories, retention periods, or the excluded-processing position in §9.

**Route One Systems** — Lagos, Nigeria
Data Protection Contact: [to be completed]
**Acknowledgement — Lagos State Traffic Management Authority**
Name: ______________________  Title: ______________________
DPO / authorised officer signature: ______________________  Date: ____________

---

**Accompanying document:** `LASTMA_TECHNICAL_PROPOSAL.md`
