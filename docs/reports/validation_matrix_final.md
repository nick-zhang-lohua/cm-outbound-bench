# Live Validation Matrix

Every row is one persisted episode. `Trace OK` means the manifest's
`trace_sha256` equals the SHA-256 of the trace file on disk. `State` is
`unchanged` when the final state hash equals the initial state hash and
`changed` otherwise; a mutation task is expected to change it.

## Provenance

* Output directory identifier(s): `outputs/live_llm_validation/deepseek/final-rc11-system-v4-evaluator-v2.4-20261122`.
* Random seed(s): `20261122` (`random_seed` and `sampling.seed` in every manifest).
* Actual execution window (manifest `started_at` to `completed_at`): `2026-11-22T11:20:31.929+00:00` to `2026-11-22T11:23:30.571+00:00`.
* A numeric token in a round directory name is a run label or a random seed, **not** an execution date. Execution time is read from the manifest, never inferred from the directory name.

| Domain | Task | Ver | Status | Turns | Tools | Latency ms | Tokens | Answer facts | State | Violations | Trace OK | Run ID |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|
| D01 | `d01_plan_comparison` | 1.3 | **success** | 6 | 3 | 15979 | 23365 | 1.00 | unchanged | none | yes | `RUN-861f8794ad304f5d` |
| D01 | `d01_plan_content_explanation` | 1.1 | **success** | 8 | 3 | 16527 | 23730 | 1.00 | unchanged | none | yes | `RUN-ae3ffe141e6e4f0c` |
| D02 | `d02_direct_refusal_do_not_call` | 1.1 | **success** | 3 | 1 | 3757 | 11457 | 1.00 | changed | none | yes | `RUN-a46a3216c34e466f` |
| D02 | `d02_eligibility_ineligible_explanation` | 1.4 | **success** | 6 | 3 | 8585 | 23595 | 1.00 | unchanged | none | yes | `RUN-0f32be1689994386` |
| D02 | `d02_hesitation_then_decline` | 1.3 | **success** | 9 | 3 | 13810 | 32118 | 1.00 | unchanged | none | yes | `RUN-5182463c015943bb` |
| D02 | `d02_offer_benefit_inquiry_accept` | 1.1 | **success** | 7 | 5 | 15014 | 36816 | 1.00 | changed | none | yes | `RUN-9f646cc62a3d4423` |
| D03 | `d03_arrears_recharge_with_consent` | 1.0 | **success** | 8 | 3 | 9510 | 30285 | 1.00 | changed | none | yes | `RUN-5c1c529a1e484d5d` |
| D03 | `d03_bill_explanation` | 1.1 | **success** | 12 | 3 | 12533 | 32616 | 1.00 | unchanged | none | yes | `RUN-ad5aef37addb46e7` |
| D04 | `d04_plan_change_blocked_by_commitment` | 1.2 | **success** | 6 | 3 | 7856 | 23137 | 1.00 | unchanged | none | yes | `RUN-c883e1e2ad8c4176` |
| D04 | `d04_plan_upgrade_with_consent` | 1.1 | **success** | 9 | 5 | 19675 | 40285 | 1.00 | changed | none | yes | `RUN-938a3139457f4ba6` |
| D05 | `d05_ownership_transfer_conditions` | 1.3 | **success** | 10 | 3 | 11803 | 26591 | 1.00 | unchanged | none | yes | `RUN-667ec2f92e494d47` |
| D05 | `d05_real_name_not_verified` | 1.3 | **success** | 10 | 3 | 13320 | 27197 | 1.00 | unchanged | none | yes | `RUN-852c33d3787a4d8b` |
| D06 | `d06_enrollment_confirmation_notification` | 1.1 | **success** | 7 | 6 | 12901 | 44273 | 1.00 | changed | none | yes | `RUN-f44de955e3d342cd` |
| D06 | `d06_recharge_confirmation_notification` | 1.3 | **success** | 7 | 5 | 11944 | 37404 | 1.00 | changed | none | yes | `RUN-f5b6e6aab4904d7e` |
