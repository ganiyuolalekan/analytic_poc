# NSW Intelligence Console: share report

> **SYNTHETIC DATA · UNOFFICIAL CONCEPT DEMO · Not endorsed by any agency**

Data as of **05 Oct 2026 18:00 WAT** · report fingerprint `1392d4a356a13869b672d1714ed4439fa65f17c391aacea17985428d21bd0c51` · model: us.openai.gpt-5.5

## 1. Cover and disclaimer
All data is synthetic. Agency names and logos illustrate structure only; fee names, rates, remittance rules, account codes and identifiers are simulated approximations. Nothing here describes real agency performance. Report generated for a conversation with the NSW secretariat; reproducible with `make report` (fingerprint above, as-of 2026-10-05T17:00:00Z).

## 2. What was built
A live, traceable, supervised financial view across NSW agencies. Needs: **speed** (clearance, target tracker, live feed), **trust** (ledger-backed statements, four-way match, trace, verified assistant), **supervision** (12 rules, reviews, audit, scorecards). Twelve pages: Command Center, Clearance Journey, Entity Explorer, Trace Workbench, Reconciliation, Supervision, Reports, Assistant, Data Quality, Architecture, Admin, Methodology. Run: `make setup probe seed run` (see README).

## 3. Data inventory
| table               |      rows |
|:--------------------|----------:|
| consignments        |    54,012 |
| stage_events        |   525,720 |
| fee_assessments     |   693,915 |
| payments            |    54,259 |
| payment_allocations |   689,951 |
| settlements         |   301,940 |
| journal_entries     |   990,826 |
| journal_lines       | 2,726,006 |
| live_events         |    80,998 |
| alerts              |        16 |

Date range: engine warm-up from 03 Jun 2026, analysis window from 01 Jul 2026 to 05 Oct 2026 18:00 WAT.

**Monthly collections by entity (assessed, ₦bn, September):**

| entity   |   value_bn |
|:---------|-----------:|
| NCS      |      48.21 |
| NRS      |      28.02 |
| NPA      |       9.29 |
| FAAN     |       1.78 |
| NIMASA   |       1.67 |
| NAFDAC   |       1.52 |
| SON      |       1.32 |
| NSW      |       0.63 |
| NAQS     |       0.46 |
| NESREA   |       0.22 |

Origin mix (top 5 by paid): CN 28%, IN 9%, US 8%, AE 8%, NL 6%. Model vs fallback: 203 of 243 plans/profiles from the model. Calibration: every entity's monthly assessed total is within ±15% of the brief's band (test `test_calibration_monthly_totals_within_bands`).

## 4. Quality and test results
# Test summary

Run at 2026-10-05 18:42:07 · 171 s · `133 passed in 169.46s (0:02:49)`

**133 passed, 0 failed** (fast and slow tests; slow tests run against `data/nsw.db`).

| test file | passed | failed |
|---|---|---|
| tests/test_assistant_golden.py | 29 | 0 |
| tests/test_phase0_llm.py | 9 | 0 |
| tests/test_phase1_core.py | 21 | 0 |
| tests/test_phase2_roles.py | 9 | 0 |
| tests/test_phase3_engine.py | 12 | 0 |
| tests/test_phase5_analytics.py | 16 | 0 |
| tests/test_phase5_beats.py | 12 | 0 |
| tests/test_phase6_ui.py | 20 | 0 |
| tests/test_resilience_perf.py | 5 | 0 |

**Assistant evaluation:** 100.0% of 62 base questions; paraphrase robustness 92.5% of 174; zero passing answers with unverified numbers; median latency 0.2 s (cached replays). See `reports/assistant_eval.md`.

**Story beats (B1-B10):**

| beat | name | detector | fired | first detection |
|---|---|---|---|---|
| B1 | Dwell-time improvement trend | R-TGT-01 | yes | 02 Jul 00:00 |
| B2 | Scanner outage at Apapa | R-PHYS-01 | yes | 12 Aug 06:00 |
| B3 | NAFDAC permit backlog | R-SLA-01 | yes | 24 Aug 18:00 |
| B4 | FX movement (NGN weakens ~6% over 5 days) | R-FX-01 | yes | 03 Sep 00:00 |
| B5 | Late remittance (one regulator, 9 days late) | R-REM-01 | yes | 13 Sep 18:00 |
| B6 | Under-assessment cluster (leakage indicator) | R-FEE-01 | yes | 10 Sep 12:00 |
| B7 | Settlement lag at one bank | R-SET-01 | yes | 19 Sep 18:00 |
| B8 | Duplicate payments burst | R-DUP-01 | yes | 02 Oct 12:00 |
| B9 | Data-quality gap (FAAN timestamps missing) | R-DQ-01 | yes | 28 Sep 06:00 |
| B10 | Holiday effect (Independence Day) | volume chart | yes | – |

**Performance:** full backfill about 160 s after plans exist; cached page loads under 2 s (test `test_cached_page_loads_meet_the_two_second_budget`); one simulated day under 5 s offline.

## 5. Model usage and cost
Model `us.openai.gpt-5.5` for every generated-content role (profiles, weekly flow and operations plans, live director, narrator, digest, assistant, storyline drafts, paraphrases). 1762 calls (546 cached; hit rate 31%), 3,614,982 tokens (3,334,348 in, 280,634 out). Estimated cost: tokens only (no price configured).

| role       |   calls |   tokens_in |   tokens_out |
|:-----------|--------:|------------:|-------------:|
| chat       |    1454 |     3168047 |       116755 |
| digest     |       1 |         183 |          142 |
| director   |       2 |         900 |          929 |
| flow_plan  |      44 |       44354 |        53620 |
| ops_plan   |     171 |       85412 |        58517 |
| paraphrase |      58 |        4368 |         5703 |
| profile    |      24 |       27956 |        37292 |
| storyline  |       8 |        3128 |         7676 |

## 6. Verified facts pack
- **S01** (speed, score 0.95): Weekly median dwell fell from 14.2 days to 11.0 days between the first and last complete weeks.  
  _evidence:_ clearance.weekly_dwell (consignments by gate-out week) · chart C1
- **S02** (speed, score 0.9): The digital share of total time fell from 42.2% to 36.8%.  
  _evidence:_ clearance.digital_physical · chart C2
- **S03** (speed, score 0.95): Counter-intuitive: digital time per consignment fell 4% (from 145 to 139 hours) while physical time fell only -20% (from 198 to 238 hours), so physical stages now dominate.  
  _evidence:_ clearance.digital_physical per-consignment hours · chart C2
- **S04** (speed, score 0.85): Truck call-up & evacuation is the largest source of delay: 104 hours per consignment, 39% of total time, and it is outside the NSW's control.  
  _evidence:_ clearance.bottlenecks (last four weeks) · chart C3
- **S05** (speed, score 0.6): Stage medians: Truck call-up & evacuation 99 hours; Terminal charges & gate pass 57 hours; Examination 51 hours.  
  _evidence:_ clearance.stage_summary · chart C3
- **S06** (speed, score 0.9): During the Apapa scanner outage the planned exam wait rose from 72 to 170 hours (2.4 times), detected at 12 Aug 06:00 WAT by alert ALT-20260812-00001; about 1378 consignment-days of extra waiting accrued.  
  _evidence:_ stage_events S06 queued rows at NGAPP; alerts R-PHYS-01 · trace: `ALT-20260812-00001` · chart C4
- **S07** (speed, score 0.85): On the current trend median dwell reaches 7 days around 2026-12-18 (status: on track); the trend slope is -0.33 days per week.  
  _evidence:_ forecast.project_to_target · chart C1
- **S08** (speed, score 0.7): Illustrative cost of dwell beyond 7 days over the last four weeks: ₦8.69bn (assumptions editable; illustrative).  
  _evidence:_ clearance.cost_of_delay
- **S09** (speed, score 0.6): Predictability: over the last four weeks median dwell was 11.2 days and p90 18.0 days.  
  _evidence:_ clearance.dwell_table
- **S10** (speed, score 0.55): Against published benchmarks (verify before quoting): current median dwell 11.0 days vs target under 7 days and a global benchmark of about 4 days.  
  _evidence:_ published figures + clearance.weekly_dwell · chart C1
- **S11** (speed, score 0.7): During the NAFDAC permit backlog, planned approval time rose from 32 to 57 hours and the SLA alert ALT-20260824-00001 was raised on 24 Aug.  
  _evidence:_ stage_events S02 queued rows (NAFDAC); alerts R-SLA-01 · trace: `ALT-20260824-00001`
- **S12** (speed, score 0.4): Independence Day (1 October) saw 84 manifests against 358 on the neighbouring weekdays.  
  _evidence:_ consignments by manifest day
- **T01** (trust, score 0.95): Since 1 July the NSW channel assessed ₦287.05bn, collected ₦285.51bn, settled ₦283.06bn to agencies and recorded ₦140.80bn remitted to the Treasury.  
  _evidence:_ reconcile.four_way · chart C5
- **T02** (trust, score 0.7): Collection efficiency was 98.6% and the cost of collection 0.68 naira per ₦100 collected.  
  _evidence:_ reconcile.four_way · chart C5
- **T03** (trust, score 0.6): ₦2.45bn is in transit (paid, not yet settled) across 6 banks; this is timing, not error.  
  _evidence:_ reconcile.in_transit_by_bank
- **T04** (trust, score 0.95): Revenue assurance caught an under-assessment cluster: NCS duty on Electronics from CN was assessed 12.1% below expectation (about ₦24.2m over 6 days); alert ALT-20260910-00001 fired on 10 Sep.  
  _evidence:_ reconcile.leakage_heatmap (NCS-DUTY, 8-13 Sep); alerts R-FEE-01 · trace: `ALT-20260910-00001` · chart C6
- **T05** (supervision, score 0.8): A burst of 14 duplicate payments worth ₦63.2m on 2 October was flagged by alert ALT-20261002-00001 at 12:00 WAT.  
  _evidence:_ payments is_duplicate; alerts R-DUP-01 · trace: `ALT-20261002-00001`
- **T06** (supervision, score 0.85): Bank C settlement lag rose from 24 to 74 hours (T+3); alert ALT-20260919-00002 was raised on 19 Sep 18:00 WAT.  
  _evidence:_ settlement_batches Bank C; alerts R-SET-01 · trace: `ALT-20260919-00002` · chart C7
- **T07** (supervision, score 0.8): One regulator remitted 9 days late (₦418.4m); alert ALT-20260913-00001 was raised on 13 Sep, three days after the due date.  
  _evidence:_ remittances; alerts R-REM-01 · trace: `ALT-20260913-00001`
- **T08** (trust, score 0.85): A two-day data gap at one operator cut record completeness from 98% to 55% and was flagged the same day (alert R-DQ-01).  
  _evidence:_ quality.completeness_trend (FAAN); alerts R-DQ-01 · chart C8
- **T09** (trust, score 0.9): All 990,826 journal entries balance (0 unbalanced) and the consolidated balance sheet balances at ₦433.04bn of assets.  
  _evidence:_ journal_lines integrity query; statements.position('ALL')
- **T10** (trust, score 0.9): Trace in seconds: consignment NSW-202609-NGONN-0000880 was charged by 6 agencies (NCS, NIMASA, NPA, NRS, NSW, SON) totalling ₦5.7m; a statement line traces to journal entries and source documents with totals tying out at every level (L1 = L2: True).  
  _evidence:_ trace.consignment_trace, trace.tie_out · trace: `NSW-202609-NGONN-0000880`
- **T11** (trust, score 0.5): NCS accounts for 52% of collections; the top origin country, CN, supplies 28% of fees.  
  _evidence:_ queries.entity_mix; queries.aggregate by origin
- **T12** (trust, score 0.6): Data confidence scores range from 83 to 99 across agencies.  
  _evidence:_ quality.scorecard
- **T13** (supervision, score 0.7): 16 alerts across 11 rules were raised and each scripted incident was detected.  
  _evidence:_ alerts table · chart C9
- **T14** (trust, score 0.85): The assistant passed 100% of 62 golden questions against an independent oracle and 93% of 174 paraphrases, with every passing answer's numbers verified against tool outputs.  
  _evidence:_ scripts/run_assistant_eval.py
- **T15** (trust, score 0.5): 203 of 243 generated profiles and plans came from the language model; the rest are deterministic fallbacks (warm-up weeks).  
  _evidence:_ generation_runs
- **T16** (supervision, score 0.5): Exception classes: mismatch 9339; duplicate 15; unpaid 357; settled_late 50; orphan_payment 14.  
  _evidence:_ reconcile.exception_summary
- **T17** (trust, score 0.55): The naira weakened 6.2% over five days in early September, raising the naira value of USD fees; alert R-FX-01 recorded it.  
  _evidence:_ fx_rates; alerts R-FX-01
- **T18** (trust, score 0.4): The database holds 54,012 consignments and 2,726,006 ledger lines from the warm-up start to now.  
  _evidence:_ row counts

## 7. Storyline A: Where the time really goes: proof of progress and the last mile
_(drafted by us.openai.gpt-5.5; every number checked against facts.json)_

### Slide 1: The target and why it matters
**Key message:** The current median dwell is moving toward the 7 days target, but the remaining gap still carries operational and illustrative cost consequences.

- Current median dwell is 11.0 days versus the target of 7 days.
- Published benchmark context is about 4 days, to be verified before external quoting.
- Over the last four weeks, median dwell was 11.2 days and p90 was 18.0 days.
- Illustrative cost of dwell beyond 7 days over the last four weeks is ₦8.69bn, with mean excess days of 4.9 days.

_Speaker notes:_ This pack uses synthetic operational data and should be read as a decision-support view. The target is clear: move current median dwell from 11.0 days toward 7 days. The cost figure is illustrative and assumption-based, so it is useful for prioritisation rather than external reporting.

![C1](figures/C1_dwell_trend.png)

_Evidence: S08, S09, S10_

### Slide 2: The trend: weekly median dwell July to now
**Key message:** Weekly median dwell has improved materially, falling from 14.2 days to 11.0 days between the first and last complete weeks.

- Weekly median dwell fell from 14.2 days to 11.0 days.
- The change between the first and last complete weeks was -22.5%.
- The current trend slope is -0.33 days per week.
- On the current trend, median dwell reaches 7 days around 2026-12-18, with status: on track.

_Speaker notes:_ The trend shows real progress rather than a static problem. The weekly median has fallen by -22.5% from the first complete week to the last complete week. On the current trajectory, the 7 days target is projected around 2026-12-18.

![C1](figures/C1_dwell_trend.png)

_Evidence: S01, S07_

### Slide 3: Anatomy of delay: stage waterfall, digital vs physical, hand-off waits
**Key message:** The largest remaining delays sit in physical flow and hand-off stages, especially truck call-up and evacuation.

- Truck call-up & evacuation is 104 hours per consignment and 39% of total time.
- Truck call-up & evacuation is outside the NSW's control.
- Stage medians are 99 hours for Truck call-up & evacuation, 57 hours for Terminal charges & gate pass, and 51 hours for Examination.
- Digital time per consignment moved from 145 hours to 139 hours, while physical time moved from 198 hours to 238 hours.

_Speaker notes:_ The process finding is neutral: the main remaining delays are concentrated in physical movement and hand-off steps. Truck call-up & evacuation is the largest measured source of delay, at 104 hours per consignment and 39% of total time. Digital time has reduced, but physical stages now dominate the end-to-end dwell picture.

![C3](figures/C3_stage_waterfall.png)

_Evidence: S03, S04, S05_

### Slide 4: The shift: digital share falling while physical dominates
**Key message:** Digital share of total time has fallen from 42.2% to 36.8%, leaving physical stages as the dominant share of dwell.

- Digital share of total time fell from 42.2% to 36.8%.
- Digital time per consignment fell 4%, from 145 hours to 139 hours.
- Physical time moved from 198 hours to 238 hours, with reported physical_fall_percent of -20%.
- The headline finding is counter-intuitive: digital time improved, while physical stages now dominate.

_Speaker notes:_ The synthetic data points to a shift in where the constraint sits. Digital time is improving, but that improvement is no longer enough to drive the whole dwell outcome by itself. The last mile is increasingly physical and operational.

![C2](figures/C2_digital_physical.png)

_Evidence: S02, S03_

### Slide 5: Case study: the Apapa scanner outage
**Key message:** The Apapa scanner outage shows how a single operational disruption can rapidly add waiting time to the system.

- During the outage, planned exam wait rose from 72 hours to 170 hours.
- The increase was 2.4 times.
- The alert was detected at 12 Aug 06:00 WAT.
- Alert ALT-20260812-00001 was raised, and about 1378 days of extra waiting accrued.

_Speaker notes:_ This case study is a concrete example of how disruption appears in the dwell data. The planned exam wait rose from 72 hours to 170 hours during the outage. The alerting point matters because it shows the system can detect shocks early enough for coordinated response.

![C4](figures/C4_apapa_outage.png)

_Evidence: S06_

### Slide 6: Projection and cost of delay
**Key message:** The trend is on track for 7 days around 2026-12-18, but delay beyond the target still has a large illustrative cost.

- Projected date to reach 7 days is around 2026-12-18.
- Status is on track.
- Trend slope is -0.33 days per week.
- Illustrative cost of dwell beyond 7 days over the last four weeks is ₦8.69bn.
- Mean excess days over the last four weeks were 4.9 days.

_Speaker notes:_ The projection is positive, but the cost of delay remains material while dwell is above the 7 days target. The ₦8.69bn figure is illustrative and based on editable assumptions. It should be used to focus attention on the highest-value interventions.

![C1](figures/C1_dwell_trend.png)

_Evidence: S07, S08_

### Slide 7: What we need to make it real
**Key message:** To make the 7 days trajectory real, action should focus on the physical last mile, disruption alerts, and sustained weekly performance management.

- Focus the last-mile agenda on Truck call-up & evacuation, which is 104 hours per consignment and 39% of total time.
- Maintain attention on high-wait hand-offs: 99 hours for Truck call-up & evacuation, 57 hours for Terminal charges & gate pass, and 51 hours for Examination.
- Use alerts like ALT-20260812-00001 and ALT-20260824-00001 to trigger coordinated response to operational shocks.
- Track the digital-to-physical shift weekly, with digital share moving from 42.2% to 36.8%.
- Keep the 7 days target tied to the projected 2026-12-18 milestone and the -0.33 days per week trend slope.

_Speaker notes:_ The evidence shows progress, but also shows that the remaining work is concentrated in the physical last mile and operational hand-offs. The recommended posture is not to assign blame, but to manage the few stages that now determine the end-to-end result. Weekly review should connect the 7 days target, the trend slope, and alert-based response.

![C3](figures/C3_stage_waterfall.png)

_Evidence: S02, S04, S05, S06, S07, S11_

## 8. Storyline B: One window, one truth: every naira traceable and supervised
_(drafted by us.openai.gpt-5.5; every number checked against facts.json)_

### Slide 1: The trust gap: many agencies, many ledgers, one question
**Key message:** The synthetic verified data shows one traceable view from assessment to Treasury remittance, supported by balanced journals.

- Since 1 July, the NSW channel assessed ₦287.05bn, collected ₦285.51bn, settled ₦283.06bn to agencies, and recorded ₦140.80bn remitted to the Treasury.
- Collection efficiency was 98.6%, with cost of collection at ₦0.68 per ₦100 collected.
- ₦2.45bn is in transit across 6 banks, classified as timing and not error.
- All 990,826 journal entries balance, with 0 unbalanced entries and consolidated assets of ₦433.04bn.
- NCS accounts for 52% of collections, while CN supplies 28% of fees.

_Speaker notes:_ This slide frames the trust gap as a reconciliation problem across the revenue chain, not as an agency performance issue. The data is synthetic and verified, and it shows that the same view can connect assessment, payment, settlement, remittance, and accounting balance.

![C5](figures/C5_four_way_funnel.png)

_Evidence: T01, T02, T03, T09, T11_

### Slide 2: The four-way match funnel and exception classes
**Key message:** The four-way match funnel turns the revenue journey into a supervised exception queue.

- The funnel links assessed ₦287.05bn, paid ₦285.51bn, settled gross ₦283.06bn, and remitted ₦140.80bn.
- Exception classes are mismatch 9339, duplicate 15, unpaid 357, settled_late 50, and orphan_payment 14.
- 16 alerts across 11 rules were raised.
- Each scripted incident was detected.

_Speaker notes:_ The funnel provides a common operating language for what has been assessed, what has been paid, what has settled, and what has reached Treasury. The exception classes make the reconciliation workload visible and actionable without assigning blame to any institution.

![C9](figures/C9_alert_timeline.png)

_Evidence: T01, T13, T16_

### Slide 3: Trace in 30 seconds: statement line to journal entries to consignment
**Key message:** A statement line can be traced through journal entries and source documents to a specific consignment with totals tying out at every level.

- Consignment NSW-202609-NGONN-0000880 was charged by 6 agencies: NCS, NIMASA, NPA, NRS, NSW, and SON.
- Total assessed value for the consignment was ₦5.7m.
- The trace links the statement line to journal entries and source documents.
- Totals tie out at every level, with L1 = L2: True.
- 206 entries were checked.

_Speaker notes:_ This is the practical meaning of one truth: a user can move from a bank statement line to the accounting entries and then to the underlying consignment. The control is evidenced by a tie-out result rather than by manual assertion.

![C1](figures/C1_dwell_trend.png)

_Evidence: T10_

### Slide 4: Leakage indicator: the under-assessment cluster
**Key message:** Revenue assurance flagged a duty-line anomaly that can be investigated as a process signal.

- Electronics from CN was assessed 12.1% below expectation.
- The estimated shortfall was about ₦24.2m over 6 days.
- The cluster covered 114 assessments.
- Alert ALT-20260910-00001 fired on 10 Sep.
- CN supplies 28% of fees, making origin-level monitoring useful for supervision.

_Speaker notes:_ This slide treats the under-assessment cluster as an assurance indicator, not as a performance label for any agency. The value is that the platform detects a pattern quickly enough to support targeted review.

![C6](figures/C6_leakage_heatmap.png)

_Evidence: T04, T11_

### Slide 5: Supervision loop: late remittance, settlement lag, duplicate payments
**Key message:** The supervision loop distinguishes timing items from exceptions that need follow-up.

- One regulator remitted 9 days late, involving ₦418.4m, with alert ALT-20260913-00001 raised on 13 Sep.
- One settlement bank’s lag rose from 24 hours to 74 hours, with alert ALT-20260919-00002 raised on 19 Sep 18:00 WAT.
- A burst of 14 duplicate payments worth ₦63.2m was flagged by alert ALT-20261002-00001 at 12:00 WAT.
- ₦2.45bn is in transit across 6 banks, and this is timing, not error.

_Speaker notes:_ The operating loop is simple: detect, classify, and route the item to the right follow-up path. Timing differences are separated from late remittance, settlement-lag, and duplicate-payment patterns so supervision remains neutral and evidence-based.

![C7](figures/C7_bank_lag.png)

_Evidence: T03, T05, T06, T07_

### Slide 6: Confidence and onboarding: data confidence, data-quality gap, cost of collection
**Key message:** Onboarding can be governed through confidence scores, completeness checks, verified answers, and collection-cost visibility.

- Data confidence scores range from 83 to 99 across agencies.
- A two-day data gap at one operator cut record completeness from 98% to 55% and was flagged the same day by alert R-DQ-01.
- The assistant passed 100% of 62 golden questions against an independent oracle.
- The assistant passed 93% of 174 paraphrases, with every passing answer’s numbers verified against tool outputs.
- Cost of collection was ₦0.68 per ₦100 collected.

_Speaker notes:_ This slide shows how onboarding quality can be monitored with measurable signals rather than narrative assurance. The same synthetic verified data supports confidence scoring, data-quality detection, answer validation, and cost-of-collection oversight.

![C8](figures/C8_completeness_gap.png)

_Evidence: T02, T08, T12, T14_

### Slide 7: How it plugs in and the ask
**Key message:** The one-window trace should operate as the supervised control plane for assessment, collection, settlement, remittance, journals, and source documents.

- The plug-in points are assessment, collection, settlement, remittance, journal entries, and source documents.
- The control fabric has 16 alerts across 11 rules, and each scripted incident was detected.
- Assurance is supported by 990,826 balanced journal entries and consignment-level tie-out with L1 = L2: True.
- The supervision queue uses the exception classes mismatch, duplicate, unpaid, settled_late, and orphan_payment.
- The ask is to adopt the one-window supervision workflow as the common traceability and exception-management view.

_Speaker notes:_ This closing slide converts the evidence into an operating ask. The data is synthetic, but the control pattern is clear: plug the feeds into one trace, use verified tie-outs, and manage exceptions through a shared supervision queue.

![C9](figures/C9_alert_timeline.png)

_Evidence: T09, T10, T13, T16_

## 9. Demo moments
**Storyline A:** Clearance Journey > target tracker > note the Apapa spike (12-15 Aug) > consignment list > Trace; then Admin (Presenter mode) > inject *Scanner outage at Apapa* with LIVE_SPEED 5 or 20 and watch the feed, situation board and the exam-wait alert.

**Storyline B:** Reconciliation > open the under-assessment exception (Electronics from China) > Trace; Supervision > alert > review > assign > resolve; Reports > Since 1 July > compute > fingerprint > export PDF; Assistant > ask: "What was total assessed vs paid vs settled across all entities in September 2026?", "Which commodity and origin pair shows the biggest assessment shortfall in September 2026?", "List high-severity open alerts"; open *How I computed this* and note the Verified badge.

## 10. Likely objections and answers
- **Data realism:** all synthetic by design; fee rules and IDs are replaceable via config; a pilot with real extracts under NDA calibrates them.
- **Sovereignty:** read-only, prompts carry aggregates only, model endpoint is swappable (hosted, in-country or local open-weight), offline degraded mode works.
- **Overlap with existing systems:** the console reads from NSW and agencies and adds a ledger-backed reconciliation, trace and supervision layer; it does not replace them.
- **Accuracy:** numbers are computed by SQL/Python, the assistant is verified number by number, and the evaluation above uses an independent oracle.
- **Procurement:** four-week pilot on one or two agencies; modular phases; open formats (CSV, Excel, PDF).

## 11. Questions to close the meeting
Clearance-time group first: (1) Which clock does the programme use for clearance time: arrival, declaration or release? (2) Can the NSW share stage timestamps for scanners, terminals and trucks? (3) Which agencies can provide a read-only extract first? (4) Who owns the end-2026 target and its definition? (5) How are remittances reconciled today and by whom? (6) What hosting rules apply (cloud, in-country, on-premises)? (7) Which reports does the Ministry of Finance receive now, and how often?

## 12. Limitations and things to verify
- Everything is synthetic; rates, rules and identifiers are simulated.
- Published benchmarks (dwell 18-21 days, global about 4, Ghana 5-7, Benin about 4, Rwanda 11 days to about 1.5, target under 7 days by end 2026) must be re-checked before quoting.
- Logos: 12 entities all matched; one extra file (Nigeria Immigration Service) is not an entity in the brief and is unused.
- `.env` variable names detected: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_PROJECT_ID; the model GPT-5.5 is served as inference profile `us.openai.gpt-5.5` on the Bedrock runtime route (see ASSUMPTIONS.md).
- Screenshots: Playwright is not installed here, so `screenshots/` lists the exact pages and clicks instead (see `screenshots/README.md`).
- Paraphrase robustness is below the base pass rate: some variants hit transient timeouts or interpret ambiguous questions differently.

## 13. Appendix
**File manifest:** `NSW_Demo_Share_Report.md/.pdf`, `facts.json`, `figures/`, `tables/`, `screenshots/README.md`, `README_share.md`.

**Reproduce:** `make setup probe seed test eval report`; as-of `2026-10-05T17:00:00Z`; simulation seed 20260701.

**Definitions:** *Dwell* = arrival to gate-out. *Clearance* = declaration filed to customs release. *Four-way match* = assessed, paid, settled, remitted with each gap classified.

**Verification log:** no unmatched numbers in the drafted text.

---
SYNTHETIC DATA · UNOFFICIAL CONCEPT DEMO · Not endorsed by any agency
