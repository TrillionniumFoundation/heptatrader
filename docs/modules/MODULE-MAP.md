# 模块地图（V2 生成视图）

Status: current generated view
Applies to: `docs/modules/module-registry-v2.json`
Verification: `python3 scripts/check_documentation_control_plane.py` and module checker
Authority: generated module inventory

| Domain | Current/Target modules |
|---|---|
| Contracts/Numeric | protocol contracts；numeric core |
| Agent/Gateway | gateway core；session authority；native client；MCP adapter |
| Intent/Decision | target-position intent；proposal aggregator；global allocator |
| Portfolio/Risk | portfolio compiler；risk policy |
| State/OMS/Recovery | state authority；OMS journal；reconcile |
| Execution | execution client；permit authority；execution core |
| Venue | deterministic simulator；IB；CTP unsupported；XT unsupported |
| Management | module registry；rollout controller |
| Reliability | runtime observability |
| Research | research protocol |

`current-composite-to-split` 表示代码已存在但 target/职责仍需进一步拆分；`planned` 模块不得出现在 runtime capability discovery。
