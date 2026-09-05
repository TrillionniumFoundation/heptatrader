# 回滚

Status: current normative
Applies to: modules, runtime deployments and release artifacts
Verification: rollback fixtures, install-tree verification, replay/reconciliation tests and protected operations process
Authority: rollback authority

回滚单位是上一份完整已验证的：

```text
artifact/archive + install manifest + SHA256SUMS + SPDX SBOM + provenance
+ canonical config source/canonical digest + state/schema migration contract
```

禁止在目标机手工替换单个 binary、shared object、schema、service unit 或配置字段来拼装版本。Rollback 不是旧代码重新获得 authority；恢复前仍必须建立新的 runtime identity、epoch/fence、journal replay 和 authoritative reconciliation。

## Trigger

以下情况默认要求停止 rollout 并评估 rollback：

- journal、state、risk、fencing、kill switch 或 safe-exit invariant失败；
- new artifact 与 approved manifest/SBOM/provenance不一致；
- startup/readiness无法收敛；
- latency/backpressure超过hard safety budget；
- schema/config/contract incompatibility；
- strategy/module rollout产生隔离外影响；
- PAPER binary/config/SDK/harness/session不再匹配qualification；
- security/credential/network boundary异常。

P1 authority failure先 contain，不等待 rollback decision。

## Preconditions

1. identify exact current and target artifact/config；
2. target artifact必须有完整、未篡改的 evidence index；
3. 验证 target 仍符合当前 capability ceiling，不能回滚到含LIVE/未资格vendor payload的版本；
4. 确认 journal/state schema向后兼容或存在已测试migration；
5. engage kill switch / close new-risk；
6. 保存当前 incident、journal、snapshot、config和systemd state；
7. 定义 rollback owner、independent approver、success/abort criteria。

无法证明 target 与当前 durable state兼容时，保持停止或 flatten-only，不执行破坏性 downgrade。

## Preserve current evidence

```bash
umask 077
rollback_root="/var/lib/heptatrader-rollbacks/$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g root -m 0700 "$rollback_root"
heptactl --version | sudo tee "$rollback_root/current-version.txt"
sha256sum \
  /usr/local/libexec/heptatrader/hepta-executiond \
  /usr/local/libexec/heptatrader/hepta-tool-gatewayd \
  | sudo tee "$rollback_root/current-binaries.txt"
sudo systemctl show \
  hepta-tool-gateway.service \
  hepta-execution-simulator.service \
  hepta-execution-ib-paper.service \
  -p ActiveState -p SubState -p MainPID -p Result \
  | sudo tee "$rollback_root/systemd-before.txt"
```

实际安装 prefix以当前install manifest为准。复制journal/state时使用应用或filesystem一致性快照并验证path/inode/size/hash稳定；不要在writer活跃时把普通 `cp` 结果称为权威备份。

## Controlled drain and stop

1. 停止新 strategy proposal/Global Decision plan；
2. invalidate尚未执行的permit/plan；
3. Gateway停止新admission；
4. Execution保留cancel/reconcile/safe-exit直到确认drain策略；
5. 保存最后accepted journal sequence和authoritative snapshot generation；
6. 停止Gateway，再停止对应Execution service/socket。

Simulator 示例：

```bash
sudo systemctl stop hepta-tool-gateway.service
sudo systemctl stop hepta-execution-simulator.service
sudo systemctl stop \
  hepta-tool-gateway.socket \
  hepta-tool-session-supervisor.socket \
  hepta-execution-simulator.socket \
  hepta-execution-events-simulator.socket
```

PAPER 先按 [Kill Switch](KILL-SWITCH.md) engage，并保留broker-owning process直到uncertain/cancel/reconcile策略明确。不要为了“干净停机”丢失callback或未知send outcome。

## Verify target artifact before install

在隔离目录验证 target：

```bash
sha256sum -c SHA256SUMS
python3 scripts/check_install_tree.py /path/to/target/staging-root
```

还必须验证：evidence-index subjects、install tree digest、SPDX/provenance Git SHA/version、`vendor_sdks_included`、capability ceiling、签名/attestation（若publish流程要求）以及target config digests。不要只检查archive hash或一个binary。

## Atomic deployment

部署平台应把完整版本安装到新不可变目录或package transaction，再原子切换一个受控version pointer/package generation。禁止原地逐文件覆盖运行目录。Systemd unit、binary、schemas、docs、VERSION、LICENSE/NOTICE必须来自同一 artifact。

配置作为独立、审核过的 snapshot部署；secret由credential authority注入，不进入artifact。Current与target config source/canonical digests都记录。

## State and contract compatibility

- contract/schema major downgrade需要显式migration/replay fixture；
- journal record不能被未知旧reader静默跳过；
- state migration必须atomic或可恢复，且保留pre-migration copy/evidence；
- fixed numeric policy、reason codes、order lifecycle、execution epoch和identity mapping不能隐式改变；
- mixed provider/consumer versions仅在compatibility matrix明确支持时存在；
- migration failure保持target未ready，不能fallback到空state。

## Restart and reconciliation

从 [Startup and Readiness](STARTUP.md) Phase 1重新开始：

1. 验证target binary/version/config；
2. 新建或明确恢复execution epoch/fence；
3. journal replay到最后durable sequence；
4. 连接simulator/qualified PAPER venue；
5. 收集完整authoritative orders/positions/executions/account snapshot；
6. 执行 [Reconciliation](RECONCILIATION.md)；
7. 验证risk policy、quote freshness、kill switch和safe-exit；
8. 先开放只读Gateway，最后才允许新风险。

```bash
sudo systemctl start \
  hepta-execution-simulator.socket \
  hepta-execution-events-simulator.socket \
  hepta-execution-simulator.service
sudo systemctl start \
  hepta-tool-gateway.socket \
  hepta-tool-session-supervisor.socket \
  hepta-tool-gateway.service
```

Service active不等于rollback成功。

## Acceptance

Rollback成功要求：

- exact target artifact/config与approved evidence一致；
- install tree和`heptactl --version`一致；
- journal replay无corrupt/unknown/poisoned state；
- old writer/capability被fence；
- authoritative snapshot完整/current；
- reconcile resolved，无venue-only/conflict/uncertain泄漏；
- risk/new-risk gate只在所有前置条件满足后打开；
- kill switch disarm经过独立operator；
- smoke、fault path和required telemetry正常；
- rollback evidence被保存并复核。

## Abort and forward-fix

遇到以下情况立即停止rollback并保持隔离：target evidence损坏、schema无法读当前state、migration不可逆失败、wrong environment/account、journal不完整、venue truth不可获得、safe-exit不确定。此时选择forward fix、持续kill switch、受控flatten或人工remediation；不能通过删除state/journal、伪造migration、降低risk或使用未经资格artifact继续。
