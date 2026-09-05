# Runtime, Research and Validation Scripts

Status: current entrypoint
Applies to: `scripts/` navigation only
Verification: `./scripts/dev_core.sh`
Authority: entrypoint only; canonical authority is `docs/development/`

The canonical developer entrypoint is [`../docs/development/LOCAL-DEVELOPMENT.md`](../docs/development/LOCAL-DEVELOPMENT.md). Module, contract and pull-request workflows are linked from the documentation index.

`scripts/dev_core.sh` is the local deterministic core gate. Validators inspect repository, documentation, schema, module, configured CMake graph, install and research boundaries. Scripts do not create product capability or override exact-revision evidence.

## Repository and documentation controls

- `check_documentation_control_plane.py`：验证 canonical docs graph、entrypoints、registries 和 generated views；
- `check_repository_integrity.py`：验证仓库边界、安装与历史隔离不变量；
- `check_systemd_documentation.py`：拒绝 systemd unit 指向未登记、历史或安装根之外的本地文档；
- `check_workflow_check_contexts.py`：拒绝模糊/重复 required check context，并要求同一 context 同时覆盖 PR 与 merge group；
- `check_required_context_projections.py`：保证 PR 与 merge-group required context 投影完全相同；
- `check_github_team_mapping.py`：保证全部 ModuleManifest owner 唯一映射到目标 GitHub teams，且 team CODEOWNERS template 无漂移；
- `verify_github_governance.py`：只读读取 live GitHub API，验证无 bypass ruleset、teams、CODEOWNERS、fresh approval、source-head 与 merge-group checks，并输出 digest-bound receipt。

前三类结构检查和治理检查的离线 hostile-negative corpus属于 repository evidence。`verify_github_governance.py` 的 live success 只能在受保护 `repository-governance` environment 中产生；缺少组织 team、规则集、只读 token、merge-group SHA 或 fresh approval 时必须失败。

## Runtime and qualification controls

- `resolve_hepta_config.py`：解析并规范化 runtime configuration；
- `check_install_tree.py`：验证安装 allowlist、权限和路径安全；
- `run_ib_paper_qualification.sh`：在受保护 real PAPER runner 上调用独立 qualifier；
- `verify_ib_paper_qualification.py`：把 qualification result 绑定 exact Git SHA、binary、SDK、harness、session 与 broker-observed evidence。

仓库脚本不能创建真实 GitHub team、安装 ruleset、提供 Broker credential、批准自身 PR、关闭外部 gap 或授予 LIVE authority。
