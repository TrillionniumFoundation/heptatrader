# 能力矩阵（V2 生成视图）

Status: current generated view
Applies to: `docs/product/capability-registry-v2.json`
Verification: `python3 scripts/check_documentation_control_plane.py`
Authority: generated human-readable capability view

本表是 registry 的可读快照；权威源为 `capability-registry-v2.json`，动态 CI/qualification 结果不手写在此。

| Capability | Code/Build | Simulator | PAPER | LIVE | Release |
|---|---|---|---|---|---|
| Typed Gateway and clients | implemented/default | active | experimental | forbidden | core |
| Execution authority/OMS/recovery | implemented/default | active | experimental | forbidden | core |
| Deterministic Simulator | implemented/default | active | n/a | n/a | core |
| Target-position preview/apply | implemented-core | active | experimental | forbidden | core |
| Portfolio compiler | implemented-core | library boundary | absent | forbidden | core |
| Global multi-Agent allocator | planned/absent | planned shadow | absent | forbidden | excluded |
| Module lifecycle control plane | planned/absent | planned | absent | forbidden | excluded |
| Research/replay | experimental/tools | offline | no mutation | forbidden | core tools |
| IB PAPER | experimental/optional | n/a | conditional | forbidden | qualified-only |
| CTP | unsupported/excluded | n/a | forbidden | forbidden | excluded |
| XT/MiniQMT | unsupported/excluded | n/a | forbidden | forbidden | excluded |
| Any LIVE execution | absent | n/a | n/a | forbidden | excluded |
