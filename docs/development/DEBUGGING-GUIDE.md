# 调试、故障定位与确定性复现

Status: current normative
Applies to: developers and operators investigating Simulator, core runtime and separately qualified IB PAPER deployments
Verification: documentation-control tests, structured reason codes, deterministic replay, fault fixtures and exact-revision CI
Authority: debugging and evidence-preservation guidance; domain truth remains with the owning runtime authority

调试的目标不是让一次调用“看起来成功”，而是在不改变事实的前提下确定：哪一个权威边界首先失效、失效绑定到哪个 exact artifact/config/session、系统是否仍允许只读或安全退出、同一输入能否确定性复现。`process alive`、systemd `active`、socket 可连接、一次 RPC 成功、TWS/IB Gateway 界面显示和 telemetry 都不能单独证明交易状态正确。

## 安全不变量

调查期间必须保持以下不变量：

- Execution 仍是唯一 venue mutation、OMS journal、order lifecycle、permit 和 reconciliation authority；
- Broker/venue observation 与 durable local journal 是两个必须独立核对的事实域；
- stable reason code 优先于自由文本；未知 reason code 在 authority boundary 上按 fail-closed 处理；
- exact source、binary、config、schema、session、execution epoch/fence 和 state generation 不得在复现过程中静默变化；
- transport timeout 或连接断开后的 mutation outcome 视为 uncertain，不能通过生成新 command ID 猜测性重试；
- 调试工具、日志、metrics、replay 和 Simulator 不能授予 PAPER/LIVE 能力；
- journal、state、credential、kill-switch 和 qualification evidence 不得为了“让测试通过”而删除或改写。

当故障可能增加风险、journal 无法证明完整、execution fence 不一致、Broker observation 不完整、position/open-order divergence、kill switch 不确定或当前 owner 不明确时，立即保持 new-risk gate closed，并按 [Incident Response](../operations/INCIDENT-RESPONSE.md)、[Reconciliation](../operations/RECONCILIATION.md) 和 [Kill Switch](../operations/KILL-SWITCH.md) 处理。

## 1. 固定 exact identity

先建立不可变调查坐标，再读取行为日志。至少记录：

```bash
set -euo pipefail
umask 077

git rev-parse HEAD
heptactl --version
sha256sum "$EXECUTION_BINARY" "$GATEWAY_BINARY"
cat "$INSTALLED_DOC_ROOT/VERSION"

python3 scripts/resolve_hepta_config.py \
  --config "$HEPTA_CONFIG" \
  --profile "$HEPTA_PROFILE" \
  --format json
```

`$EXECUTION_BINARY`、`$GATEWAY_BINARY`、`$INSTALLED_DOC_ROOT`、`$HEPTA_CONFIG` 和 `$HEPTA_PROFILE` 必须来自当前 install manifest 或受控部署记录，不能凭工作站路径猜测。配置解析结果保存 `source_sha256` 与 `canonical_sha256`；不要把 credential、session token、完整 account ID 或 secret-bearing environment 写入 evidence。

对于正在运行的服务，额外记录：

```bash
systemctl show "$EXECUTION_UNIT" "$GATEWAY_UNIT" \
  -p FragmentPath -p MainPID -p ExecMainStartTimestampMonotonic \
  -p ActiveState -p SubState -p Result

stat -Lc 'type=%F owner=%U group=%G mode=%a links=%h dev=%d inode=%i size=%s' \
  "$OMS_JOURNAL"
```

服务重启、binary/config 替换、journal inode 变化、session owner/epoch 变化或 PR head/base 移动都会建立新的调查 identity；旧日志和旧绿灯只能作为历史线索，不能证明新 revision。

## 2. 按权威顺序建立事实

调查顺序固定为：

```text
exact artifact/config identity
-> Execution epoch/fence and durable journal
-> venue/Broker observation window
-> reconciliation classification
-> authoritative snapshot generation
-> Gateway/session projection
-> telemetry and free-form logs
-> strategy/model behavior
```

从 telemetry 或策略输出反推权威事实容易把缓存、延迟或采样误当成 domain truth。只有 journal replay、current Execution owner、完整 venue observation、reconciliation 和 snapshot currentness 建立后，才分析策略、性能或用户界面。

## 3. Reason-code-first 分诊

权威 reason code 来自 [`reason-code-registry-v1.json`](../verification/reason-code-registry-v1.json)。先记录完整 code、owner module、request/command/session identity 和首次出现时间，再阅读相邻自由文本。

| Prefix | 首要 owner | 第一检查点 |
|---|---|---|
| `DOC_`, `MODULE_` | Documentation Control | exact repository tree、registry、generated view、source/CMake ownership |
| `DECISION_SNAPSHOT_` | Execution | before/after epoch、fence、watermark、generation、required component currentness |
| `INTENT_` | Execution | plan/permit lifetime、snapshot generation、risk result、journal admission |
| `RISK_` | Risk Policy | fixed numeric input、quote freshness、complete state、limit hierarchy、strict reduction |
| `OPT_` | Global Decision | immutable proposal set、policy/snapshot identity、feasibility、bound/gap、deadline |
| `EXEC_` | Execution | command ID/payload digest、journal state、fence、venue support、uncertain outcome |
| `RECON_` | Execution/Reconciliation | local-vs-venue classification、observation completeness、position/order convergence |
| `MARKET_AUTHORITY_`, `MARKET_RECEIPT_` | Market Data | issuer/lifecycle/audience/nonce、clock、epoch、lineage、current source state |
| `FEATURE_` | Feature | input receipt、source generation/digest、gap/stale state、capacity and numeric result |

同一 incident 出现多个 code 时，以最早的 authority-boundary failure 为主因候选；后续 stale、timeout 和 cache errors 可能只是派生结果。未知或未注册 code 不能转换成 success、warning 或 permissive default，应保存原始值并升级给 owning module。

## 4. 只读运行时探针

只有在受控 session 已建立、token 通过安全注入且不会进入 shell history、日志或 evidence 时才运行：

```bash
export HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock
export HEPTA_TOOL_SESSION_TOKEN='<controlled-injection>'

heptactl tools list
heptactl call system.get_health
heptactl call orders.list
heptactl call portfolio.list_positions
heptactl call account.get_summary
heptactl call risk.get_limits
heptactl watch snapshot EUR.USD
```

探针输出必须关联 current session、execution epoch/fencing generation、event watermark、state/snapshot generation 和 observed-at/freshness。`tools list` 只证明 discovery；单次 health/read 只证明该响应在其 context 下通过验证。它们不能替代 journal 或 venue reconciliation。

完成后从环境移除 token：

```bash
unset HEPTA_TOOL_SESSION_TOKEN
```

不要在 `set -x`、process argument、issue、PR comment、CI log 或 evidence archive 中暴露 token。

## 5. Session、lease、epoch 与 fencing

Session 故障优先核对：

1. 当前 agent/session/peer UID 与声明 audience；
2. lease token 是否来自当前 issuer；
3. lease generation、expiry 和 authority-owned clock；
4. predecessor token/generation 是否已 fence；
5. Execution account/domain owner scope；
6. durable lease store 的 owner、mode、identity、decrypt/parse/persist 状态；
7. restart 后是否建立新 epoch 或按契约证明 continuation；
8. PAPER finalization 是否进入不可逆 tombstone 状态。

典型错误模式：

| 现象 | 必须验证 | 禁止做法 |
|---|---|---|
| stale owner 仍发请求 | current epoch/fence、peer UID、lease generation | 延长旧 lease 或忽略 generation |
| heartbeat 存活但请求拒绝 | expiry、audience、scope、renew durable result | 把 TCP/process liveness 当 authority |
| restart 后旧 token 失效 | new epoch、predecessor fence、lease-store replay | 复制旧 token 到新进程 |
| rotate 后两个 token 都出现 | atomic durable generation、predecessor fence | 任选一个继续运行 |
| finalization 后无法 provision | terminal tombstone 和 finalization IDs | 清理 tombstone 恢复交易 |

Session 调试遵循 `hepta.session-supervisor.v1`。Transport failure after submission 视为 uncertain，客户端查询 authoritative supervisor state；不得通过换 token、换 generation 或重建 session ID 掩盖未知 transition。

## 6. OMS journal、idempotency 与 uncertain command

先验证 journal 文件 identity、权限和 replay 健康，再按 command ID 查询完整 lifecycle。不得只 grep 最后一条状态。

诊断性离线检查可使用：

```bash
python3 scripts/verify_oms_journal_replay.py \
  --journal "$OMS_JOURNAL"
```

该脚本是调查辅助，不是运行时 authority，也不能替代 Execution 的严格 replay、poison detection 和 reconciliation。任何 malformed/truncated/unknown record、sequence conflict、same command ID/different payload digest、write/fsync error 或 inode/path identity 变化，都保持 risk-increase closed。

Uncertain mutation 的处理顺序：

```text
original command ID + normalized payload digest
-> durable local outcome
-> exact venue correlation/open order/execution
-> duplicate/out-of-order lifecycle merge
-> resolved terminal state or explicit unresolved isolation
```

不能因为没有收到 callback、client timeout、socket close 或 UI 未显示订单就断言“没有下单”。不得创建 replacement command/order 直到 authoritative coordinator 根据契约证明原请求未被接受并允许 retry。

## 7. Market Data、Feature 与 snapshot generation

Market Data/Feature 故障至少保存：

- venue/instrument key；
- issuer identity、lifecycle epoch、audience、nonce；
- producer epoch/sequence 和 gap state；
- source event digest、snapshot digest、feature-set version；
- source/feature generation；
- authority-owned observed-at、freshness window 和 clock rollback latch；
- vector 参与 shard 集及 coherent cut identity；
- reader/consumer binding 和拒绝 reason code。

排查顺序：

1. 输入 fixed numeric、quote relation、identity 和 timestamp 是否可验证；
2. producer epoch/sequence 是否 duplicate、regression 或 gap；
3. receipt 是否由当前 store/issuer/lifecycle/audience 签发并仍 current；
4. snapshot canonical digest 是否与内容重算一致；
5. Feature commit 前 source lineage 是否仍 current；
6. read 时 generation、expiry、clock 和 source lineage 是否仍 current；
7. queue/coalescing 是否触发容量或 gap 行为。

禁止手改 snapshot、填补缺 tick、清除 gap flag、用 wall clock 替换 authority clock、序列化同进程 capability 作为网络证明，或在 stale/gap 情况下让 Feature/Decision fallback 到旧值。

## 8. Reconciliation 与 Broker/venue 事实

以下情况直接进入 reconciliation：startup/restart、disconnect/reconnect、unknown send outcome、duplicate/out-of-order callback、journal replay fault、position/open-order divergence、epoch/fence 变化、rollback、kill-switch disarm 前和 PAPER qualification scenario。

依据 [Reconciliation](../operations/RECONCILIATION.md) 把每个对象分类为 `matched`、`local-pending`、`venue-only`、`local-only-terminal`、`duplicate`、`conflict`、`stale-epoch`、`correction` 或 `unresolved`。完整 venue observation window 必须有明确 start/end/currentness 或 complete marker；callback backlog 未排空、source stale 或 observation timeout 时，未出现对象不能当作不存在。

只有 journal not poisoned、venue window 完整、所有 object 分类完成、position/required account inputs 一致、uncertain command 终结或被安全隔离、current epoch/fence 与 snapshot/permit 一致时，才允许 `RECON_RESOLVED`。之后仍按 [Startup and Readiness](../operations/STARTUP.md) 发布新的 snapshot generation、使旧 proposal/plan/permit/cache 失效并重新评估 risk。

## 9. 确定性 replay 与最小复现

先使用不含 credentials、真实 account IDs 或未授权 market data 的最小 immutable fixture。记录 source SHA、seed、dataset/scenario digest、feature/policy/schema version、compiler、build type 和 environment。

标准核心循环：

```bash
./scripts/dev_core.sh
```

可靠性与 AddressSanitizer/UndefinedBehaviorSanitizer 路径：

```bash
CXX=g++ ./scripts/reliability_core.sh
CXX=clang++ ./scripts/reliability_core.sh
```

研究 manifest 完整性：

```bash
python3 research/run_protocol.py verify \
  --manifest research/manifest-v1.json
```

针对单个 CTest 的复现使用已有受控 build directory：

```bash
ctest --test-dir "$BUILD_DIR" \
  --output-on-failure \
  -R '^hepta_<exact_test_name>$' \
  --repeat until-fail:100
```

`--repeat until-fail` 只用于暴露不稳定性，不能用“第 100 次通过”覆盖任何失败。确定性故障必须保留第一个 divergence index、输入 digest、seed、event order、expected/actual state 和 compiler/toolchain。历史绿灯不能替代当前 exact head 或 merge candidate。

## 10. Concurrency、ordering 与 backpressure

并发调查必须区分：

- producer/consumer ordering；
- shard owner 与 shard key；
- queue count/bytes、high-water mark 和 overflow policy；
- canonical multi-lock order；
- lifecycle epoch/fence；
- authority-owned clock；
- blocking I/O 允许边界；
- safe-exit lane 是否被普通 admission 饿死。

收集 thread ID/name、owner/shard key、event/command ID、queue depth、sequence/generation 和 monotonic timestamp；不要通过添加无界 sleep、扩大队列、关闭 timeout 或把 lossless authority events 改成 lossy 来“修复”race。

Thread race 需要在仓库既有 TSan/hostile concurrency lane 或独立受控构建中复现。ASAN/UBSAN 通过不等于没有 data race，TSan 通过也不证明 crash/durability/ordering 正确。修复后必须同时运行相关确定性行为测试、sanitizer 和 exact merge-candidate gate。

## 11. GDB、core dump 与系统调用定位

只在隔离的 Simulator/开发 profile 或经事故流程批准的非 mutation 复现场景中使用 debugger。不要把 debugger 附加到仍允许新风险的 PAPER Execution。

```bash
gdb --args "$DEBUG_BINARY" "$@"
```

进入 debugger 后优先保存：

```text
thread apply all bt full
info threads
info registers
```

对于 journal、socket、permission 和 path-identity 问题，可在隔离复现进程上使用系统调用跟踪，但输出可能包含路径和 payload；先定义 redaction，再进入 evidence。Core dump、heap、environment 和 command line 可能包含 secret/token/account material，默认按敏感证据存储，权限至少 `0600`，不得上传公共 CI artifact。

## 12. 性能故障不是先调阈值

性能回归先确认 correctness、fixture identity 和环境一致，再对比完整 p50/p95/p99/p999/max、sample count、queue/load 和 compiler/toolchain。按照 [Performance Qualification](../operations/PERFORMANCE-QUALIFICATION.md)：

1. 保留 exact failing distribution；
2. 确认 baseline 与 fixture 匹配；
3. 定位 allocation、lock、I/O、queue、cache 和 instrumentation 变化；
4. 修复实现后在同 revision 重跑；
5. baseline 变更需要独立证据和审阅。

禁止删除 outlier、只报平均值、缩小 representative input、降低 sample count、扩大 threshold、混合不同 host 结果，或把 repository-CI fixture 冒充 target-host/PAPER SLA。

## 13. Evidence bundle

每个 P1、uncertain mutation、reconciliation divergence、journal poison、fence disagreement 或资格失败至少保存：

```text
incident/reproducer ID and investigator identity
exact source/base/merge-candidate SHA
binary, config source/canonical, schema and fixture digests
host/kernel/compiler/build identity
service unit and process identity
session, execution epoch/fence, watermarks and generations
journal path/inode/size/digest/health without secret payload expansion
venue observation window and object classification counts
stable reason codes and first-failure timestamp
minimal reproduction command and immutable input digests
expected/actual state and first divergence index
relevant test/workflow/run/check identity
containment, safe-exit and rollback state
```

使用权限受限目录：

```bash
set -euo pipefail
umask 077
EVIDENCE_DIR="${RUNNER_TEMP:-/tmp}/heptatrader-debug-${INCIDENT_ID}"
mkdir -p "$EVIDENCE_DIR"
chmod 0700 "$EVIDENCE_DIR"
```

Evidence 不得包含 raw credential、session token、private key、完整 account ID、无限制 market payload、未脱敏 core dump 或可重放 capability。保存命令及其 exit code；不要只保存截图。任何脱敏都要保留字段存在性、类型、digest 和 correlation identity，不能改变故障语义。

## 14. 退出与升级条件

调查只有在以下之一成立时结束：

- 根因已绑定到 exact source/config/environment，修复有正向、负向、fault-path 和相关性能/并发证据，并在同一 revision 通过必要门禁；
- 故障被证明是外部资格、组织治理或目标主机问题，已经保持能力关闭并产生不可伪造的 handoff；
- 状态仍不确定，系统维持 terminal latch/new-risk closed，evidence 完整移交 owning team。

以下不是完成条件：进程重启后暂时正常、删除 state 后测试通过、换 command ID 后成功、单次 Broker reconnect、某个 dashboard 恢复绿色、旧 PR/commit 曾经通过，或 Simulator 无法复现真实 PAPER 问题。

## 禁止的“修复”

绝对禁止：

- 删除、截断或手改 OMS journal、lease store、snapshot、status、receipt 或 qualification evidence；
- 改变原 command ID、payload digest、epoch、generation 或 owner identity 来绕过冲突；
- 关闭或放宽 risk、kill switch、fencing、freshness、schema、signature、path identity 或 reconciliation gate；
- 直接调用 Broker API 绕过 Gateway/Execution；
- 把 unknown、NaN/Inf、缺失 account/position/order、stale quote 或不完整 observation 当作零；
- 把 mock、Simulator、手写 JSON、截图、TCP connect 或历史 receipt 作为当前 PAPER/LIVE 资格；
- 把调试凭证、token、private key 或完整账户信息提交到 Git、CI、issue 或公共 artifact；
- self-approve、administrator bypass、直接推送受保护基线，或把没有 live receipt 的治理/资格 gap 标记为 closed。

调试必须减少不确定性，而不是删除证明不确定性的证据。
