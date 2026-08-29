# IB 10-Minute Regression Report (Template)

- Run ID: {{RUN_ID}}
- Overall: **{{OVERALL}}**
- Start: {{START}}
- End: {{END}}
- Duration target: {{DURATION}}
- DryRun: {{DRY_RUN}}

## Checks
- **market_tick_present**: **{{PASS_FAIL}}** — {{DETAIL}}
- **no_fatal_crash**: **{{PASS_FAIL}}** — {{DETAIL}}
- **heartbeat_present**: **{{PASS_FAIL}}** — {{DETAIL}}
- **position_summary_consistency**: **{{PASS_FAIL}}** — {{DETAIL}}
- **breaker_not_permanently_tripped**: **{{PASS_FAIL}}** — {{DETAIL}}
- **latency_report_if_enabled**: **{{PASS_FAIL}}** — {{DETAIL}}

## Key excerpts
- [market_tick] {{EXCERPT}}
- [fatal_scan] {{EXCERPT}}
- [heartbeat] {{EXCERPT}}
- [position_summary] {{EXCERPT}}
- [breaker] {{EXCERPT}}
- [latency_report] {{EXCERPT}}

## Artifacts
- run dir: {{RUN_DIR}}
- stdout: {{OUT_LOG}}
- stderr: {{ERR_LOG}}
- meta: {{META_LOG}}
- oms journal: {{OMS_PATH}}
- reconcile report: {{RECONCILE_PATH}}
- observability jsonl: {{OBS_PATH}}
- latency report: {{LAT_REPORT_PATH}}
