# RUNBOOK - STARTUP

适用范围：HeptaTrader paper/准生产环境日常启动。

## 0.0 Agent mutation 必须走远程 Execution

`HEPTA_TOOL_ALLOW_TRADE=1` 与 local Execution fallback 不兼容。进程启动时必须同时
提供有效的 `HEPTA_EXECUTION_REMOTE_MODE=SIMULATOR|PAPER`、mutation/event 两个 Unix
socket 和固定 Execution Service UID；缺少任一项都以
`EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS` fail closed。WATCH/read-only
工具仍可在 monolith 离线集成测试中使用本地只读 composition；生产 unit 已切换到独立
`hepta-tool-gatewayd`，该 daemon 即使是 WATCH 也要求远程 Execution Service，并且
二进制不链接 broker adapter、strategy 或本地下单 authority。

Execution IPC 当前为 `HEX v6`。Gateway 只发送经过 session authentication 的
Agent/session/account/request context，不再发送 Agent decision-lease token/generation。
Execution Service 在通过 `SO_PEERCRED`、daemon identity pair 和 readiness gate 后，
自行获取/续租 domain/account/instrument lease，并只把该凭据注入内部 coordinator。
HFC credential 只参与 daemon fencing generation，不能再当作 Agent lease。新下单还
必须先由同一 Execution Service 依据当前权威行情和 risk/eligibility 签发最长 5 秒、
单次使用的 preview permit；permit 精确绑定完整 owner context 与 normalized order
intent，Gateway 只能透传，不能本地签发或复用。已有 durable `Accepted` 或
`Uncertain` record 的完全相同原请求可在重启/响应丢失后以同一 command ID 绕过已消费
permit 进行幂等 resolve/replay；Accepted 返回成功 duplicate，Uncertain 继续按 durable
ledger 解析且不得制造第二次 venue send。新请求、已拒绝请求或任一 owner/intent 字段
变化的请求必须重新 preview。preview 自身使用独立 read `tool_call_id`；Execution Service 同时返回
未来 place 专用的 `command_id`，MCP 必须把它原样映射为 place 的 `tool_call_id`，
并在响应丢失后用同一值精确重试。permit record 同时绑定该未来 mutation ID，但
normalized owner/intent fingerprint 不误绑 preview read ID。每个 Agent/session 最多保留 8 个、daemon 全局
最多保留 128 个未消费 permit；同一精确 owner/intent 重新 preview 会原子替换旧 permit。

该切换不授权 PAPER/LIVE，不启动服务，也不放宽 kill switch、risk、OMS 或 reconcile gate。

Round22 起 combined soak schema 升级为 `hepta.execution-gateway-soak.v6`。除原六套件
证据外，必须精确证明 `agent_lease_not_on_wire`、`service_lease`、`multi_instrument`、
`gateway_restart`、`session_rotate` 与 `mutation_tools_remote_only`；v5 报告只能作为
Round18-Round21 历史证据，不能证明当前 HEX v6 边界。

## 0.0.1 Round23 独立 Tool Gateway 与 sessionctl

被动安装组件为 `hepta-agent-os-runtime`，只安装：

- `/usr/libexec/hepta-tool-gatewayd`；
- `/usr/bin/heptactl` 与 `/usr/bin/hepta-sessionctl`；
- `hepta-tool-gateway.service`、Agent-exclusive tool socket 与 OS-only supervisor socket；
- `/usr/libexec/hepta-mcp-server` 和同一份 Codex/OpenClaw 兼容 plugin bundle；
- WATCH-only、无 secret 的环境示例和架构文档。

安装不会创建用户、写 `/etc`、生成 token、启用或启动 unit。正式 provisioner 必须创建
固定 Gateway/Agent UID、root-owned `/etc/heptatrader/hepta-tool-gateway.env`、`0400`
supervisor lease key，以及至少 24 字节、`0600` 的 Agent session token 文件。

默认示例固定 `HEPTA_TOOL_ALLOW_TRADE=0`、`HEPTA_TOOL_SESSION_TEMPLATES=watch`。如未来
通过独立 PAPER gate，必须同时显式设置 `watch,paper`、非零 order/rate limits 和
server-owned FX/CASH contract bindings，例如：

```text
HEPTA_TOOL_ALLOW_TRADE=1
HEPTA_TOOL_SESSION_TEMPLATES=watch,paper
HEPTA_TOOL_MAX_ORDER_QTY=25000
HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN=2
HEPTA_TOOL_CONTRACT_BINDINGS=EUR.USD|EUR|CASH|IDEALPRO|USD;GBP.USD|GBP|CASH|IDEALPRO|USD
```

这些字段不授权 PAPER；它们只定义通过其他 PAPER gates 后可签发的 OS session 上限。

### SHADOW WATCH host bootstrap 与 custodian

在任何 profile deployment、WATCH activation 或 custodian provision 之前，先从已冻结的
combined runtime 生成专用 SHADOW host projection。通用 Agent runtime 仍是 94 个
`/usr` 文件，combined runtime 是 102 个 `/usr` 文件；只有该专用 projection 是 95 个
文件，并额外包含唯一的
`etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json`。该文件必须是
`0600`、257 bytes、SHA-256
`4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435`，且内容为
`paper_authorized=false`、`live_authorized=false`、空 identities。不得把任意其他
`/etc`、PAPER authority、credential 或 host state 加入 archive。

在离线构建目录生成 projection 与自绑定 install manifest；所有输出路径必须预先不存在：

```bash
python3 scripts/build_hepta_shadow_runtime_archive.py \
  --runtime-package "$RUNTIME_PACKAGE" \
  --runtime-manifest "$RUNTIME_MANIFEST" \
  --output "$SHADOW_ARCHIVE"

python3 scripts/build_hepta_shadow_install_manifest.py \
  --archive "$SHADOW_ARCHIVE" \
  --source-baseline-sha256 "sha256:$SOURCE_BASELINE_MANIFEST_SHA256" \
  --installer scripts/hepta_shadow_host_installer.py \
  --output "$SHADOW_INSTALL_MANIFEST"
```

只有 frozen source、archive、manifest 与 installer 的独立 verifier 全部通过后，才可把
精确 artifact 复制到 root-owned mode `0700` 的专用目录，并把 archive/manifest 设为
root:root `0600`。当前 round95 consumer 只接受下面三个固定 generation 路径；宿主安装
必须调用那一份冻结 installer，且 backup/receipt 预先不存在：

```bash
sudo /usr/bin/python3.12 -I -S "$FROZEN_INSTALLER" \
  --archive "/var/lib/hepta/shadow-runtime-install-artifacts/hepta-p1-round95-shadow-runtime.tar.gz" \
  --manifest "/var/lib/hepta/shadow-runtime-install-artifacts/hepta-p1-round95-shadow-runtime.manifest.json" \
  --archive-sha256 "sha256:$SHADOW_ARCHIVE_SHA256" \
  --source-baseline-sha256 "sha256:$SOURCE_BASELINE_MANIFEST_SHA256" \
  --installer-sha256 "sha256:$FROZEN_INSTALLER_SHA256" \
  --expected-current-install-generation "$CURRENT_INSTALL_GENERATION" \
  --expected-current-install-pointer-sha256 "sha256:$CURRENT_INSTALL_POINTER_SHA256" \
  --backup-root "/var/lib/hepta/shadow-runtime-backups/hepta-p1-round95-generation20-passive" \
  --receipt-output "/var/lib/hepta/shadow-runtime-install-receipts/hepta-p1-round95-generation20-passive.json" \
  --receipt-reader-gid 1000 \
  --domain alpha
```

generation `0` + `absent` 只允许真正的首次安装。当前 round95 是既有 generation 3 的
后继安装，必须在计划阶段从 current pointer 冻结其精确 generation（当前宿主应为 `3`，
成功后必须精确为 `4`）与整文件
host-specific `sha256:` digest，并通过上述两个变量传入；不得把示例值、其他宿主 digest
或 `absent` 代入。锁内观察与调用方冻结值不完全一致时，installer 必须在任何 payload、
backup 或 receipt mutation 前失败，不能把旧 archive 自动重基为更高 generation。
跨 generation 的路径集合只能做经过 predecessor manifest 验证的 append-only 扩展：旧路径
必须全部保留且 digest 与 current pointer 完全一致；任何删除、同数量替换或无法验证的旧
manifest 都在 mutation 前以 `INSTALL_CURRENT_PATH_SET_DRIFT` 拒绝。

成功 receipt 必须是 root:GID-1000、regular、single-link、`0440`；manifest 与 current
pointer 必须保持 root:root、regular、single-link、`0600`。独立计算两份文件 digest 后，
profile deployment 和全新 activation 的唯一序列是：

profile safety preflight 对每个 WATCH boundary unit 只接受 exact
`loaded+inactive/dead+no-job` 或 exact `loaded+failed/failed+no-job`；后者是无活跃进程/任务的
fail-closed 终态，不需要用临时 `systemctl reset-failed` 绕过。preflight before/after 必须
保留完全相同的状态；`active`、`activating`、排队 job 或任意混合状态仍立即拒绝。

collector 的固定 `StateDirectory=hepta-shadow-watch-alpha` 与其唯一 `private` 子目录都必须
精确为 uid/gid 2104、`0700`。consumer 仅对这一条固定路径使用专用两级 no-follow 锚定：
`/var/lib` 仍须 root-owned，StateDirectory 清单必须精确只有 `private`，而 `private` 必须为空；
父、叶两个已打开 fd 的完整元数据还必须在最终 canonical reopen 后保持一致。该检查只证明
时点空清单，不声明 continuity；不得把它泛化为允许其他非 root ancestor。

```bash
sudo /usr/bin/python3.12 -I -S \
  /usr/libexec/hepta-p1-watch-profile-deployer \
  --expected-install-manifest-sha256 "sha256:$SHADOW_INSTALL_MANIFEST_SHA256" \
  --expected-install-receipt-sha256 "sha256:$SHADOW_INSTALL_RECEIPT_SHA256" \
  --expected-prior-profile-receipt-sha256 \
    "sha256:3904f17a444fb7a6a482b187c081c9a8eba854d39dd476ff948477eb7b9376aa"

sudo /usr/bin/systemctl start hepta-p1-watch-activation.service
```

profile transaction 只重新见证已经存在的 alpha WATCH profile，不写入、不替换也不
exchange 该 profile。它必须原位保留 round86 v6 receipt、backup 与 retained target，并只用
`RENAME_NOREPLACE` 原子发布
`/var/lib/heptatrader/p1-watch-profile-receipts/round95-generation20.json`。新 receipt 必须是 v7、
`OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED`，且绑定当前 generation `4`；第三个 CLI pin 是
旧 receipt 的整文件 digest，不是 body digest。

不得复用旧成功 activation receipt；旧 `p1-watch-activation-receipt-v1.json` 与
`p1-watch-activation-receipt-v2.json` 都必须不存在。与此不同，下面这份 round86 失败终态是
round95 的强制、不可变 predecessor，不是需要清理的 poison：

- receipt：`/var/lib/hepta/shadow-observation/p1-watch-activation-failed-receipt-v1.json`
- file SHA：`sha256:957559d6a0ae12433c3ec59aee5bc4707c4c8dda2af74a0babed8da65d7dba15`
- body SHA：`sha256:22abc6d6316e9a0576e782957c886033acc50c1e97ba97d5a7a417b8274d03f7`
- journal：`/var/lib/heptatrader/p1-watch-activation/round86/journal`
- journal digest：`sha256:9b20db0e816e10dab879411ee9b255adae7d6760e159c6fbfb38b61447c8ffa6`

不得删除、重命名、重写或 touch predecessor。round95 的新成功 receipt 固定为
`p1-watch-activation-round95-receipt-v3.json`；本轮新的 failed/replacement/pending 分别为
`p1-watch-activation-round95-failed-receipt-v2.json`、
`.p1-watch-activation-round95-failed-receipt-v2.replacement`、
`p1-watch-activation-round95-pending-receipt-v2.json`。新 state、lock 与 stale quarantine
分别固定为 `/var/lib/heptatrader/p1-watch-activation/round95`、
`/var/lib/heptatrader/.p1-watch-activation-round95.lock` 与
`/var/lib/hepta/p1-admission/quarantine/activation-round95`。执行这些命令仍只建立
SIMULATOR/WATCH、deny-all broker 边界，不得由此进入 PAPER。

installer 从第一次 safety preflight 到 receipt、current-install pointer 发布或失败处理期间持续持有
`/var/lib/hepta/.shadow-runtime-install.lock`；它是 persistent 的 root:root、regular、
`0600`、single-link、size 0 文件，不得清理。成功前后的两份 preflight 必须完全相同，
包括九个 PAPER unit 以及 Gateway、activation/reconcile、WATCH custodian/collector/export
阻断单元的逐项 inactive/failed/unknown 状态（`unknown` 表示该 unit 未安装）；任一
WATCH/Gateway authority 单元活跃时安装立即拒绝。
receipt v4 明确
`preflight_continuity_claimed=false`。普通失败在锁仍绑定时精确回滚，并保持 canonical
identity 为 deny-all。若命名锁与 held inode 失绑定，只允许把可证明属于本事务的 receipt
撤入 retained quarantine、重断言 deny-all 并停止；不得无锁恢复或清理 `/usr`。此时的
任何 residue 均为失败证据，必须由新的 stable-lock recovery transaction 处理。

所有受支持的 privileged installer 必须遵守同一 persistent lock；绕过锁的 root 或
`CAP_DAC_OVERRIDE` writer 属于 host-root compromise，不在此 userspace installer 的威胁
模型内。Linux 不提供针对任意 rogue-root name replacement 的 inode-conditional unlink，
不得把重复的 check-then-unlink 描述为该原子保证。

每次成功安装还必须在同一锁下原子更新固定的
`/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json`。该文件为
root:root、regular、single-link、`0600`，其单调 generation 精确绑定当前 manifest/receipt
路径与文件 digest、source/archive/installer lineage、95-file path digest、backup root 和
全 false 权限边界。后续受支持的安装即使保留旧字节，也会推进该 generation，使旧
receipt/manifest 不能再充当“当前安装”。pointer 发布失败时，installer 先精确清理本次
receipt 并完整回滚 payload；仅当两步均成功后才恢复旧 pointer。任一步结果不确定时必须
保留 candidate pointer，使新旧 generation 都不能被 consumer 接受，并把 residue 交给
stable-lock recovery transaction。

该安装只投影被动文件：不 reload/start/enable systemd，不创建 WATCH session，不签发
continuity，也不授权 PAPER/LIVE。集成 consumer 已由 profile deployer、activation
transaction 与 admission launcher 共用：调用方先用外部 manifest pin 独立验证 canonical
manifest 和 installer member，再执行冻结 installer consumer；consumer 以 install lock 为
最外层，精确验证 receipt v4、current pointer、named lock inode/metadata、完整 95-file
closure、deny-all identity 与精确 predecessor edge，并产出 consumption-evidence v3。profile CLI 必须同时提供
`--expected-install-manifest-sha256`、`--expected-install-receipt-sha256` 与
`--expected-prior-profile-receipt-sha256`；锁顺序固定为 install lock →
profile/activation lock。当前 round95 的 profile、activation 与 admission 都必须精确绑定
generation `4`；admission 在最终 receipt 封口前再做一次验证。上述证据仍只是当前被动安装与时点验证，
不是 continuity、PAPER 或 LIVE 授权。

只有在全新的职责分离 rootful 双域 gate 为 GO、round95 activation v3 已成功、所有新的
round95 failed/replacement/pending 路径均不存在之后，才允许从下面这一条 reviewed 外层
入口启动 P1。不得直接执行 launcher，也不得改成 `python3 launcher.py`：

```bash
sudo /usr/bin/systemd-run \
  --quiet \
  --collect \
  --unit="hepta-p1-shadow-admission-round${FORMAL_ROUND}.service" \
  --uid=root \
  --gid=root \
  --service-type=exec \
  --property=Restart=no \
  --property=RemainAfterExit=no \
  --setenv=PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  --setenv=LANG=C \
  --setenv=LC_ALL=C \
  --setenv=PYTHONNOUSERSITE=1 \
  --property='Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket hepta-execution-ib-paper@alpha.service hepta-execution-ib-paper@alpha.socket hepta-execution-events-ib-paper@alpha.socket hepta-ib-paper-domain-preflight@alpha.service hepta-ib-paper-campaign-operator@alpha.service hepta-ib-paper-campaign-operator@alpha.socket' \
  -- \
  /usr/libexec/hepta-p1-shadow-admission-launcher \
  --probe-campaign-id \
    "hepta-p1-shadow-load-probe-round${PROBE_ROUND}-${CAMPAIGN_DATE}" \
  --formal-campaign-id \
    "hepta-p1-shadow-soak-round${FORMAL_ROUND}-${CAMPAIGN_DATE}" \
  --formal-start-ms "${FORMAL_START_MS}"
```

`FORMAL_ROUND` 必须精确等于 `PROBE_ROUND + 1`，两个 campaign 的八位日期必须相同，
unit round 必须等于 `FORMAL_ROUND`。`FORMAL_START_MS` 是 fresh formal-history 的
`warmup_start_ms`，不是 launcher dispatch，也不是正式 decision window 的起点。policy
必须精确满足
`valid_after_ms = decision_window_start_ms = FORMAL_START_MS + 12600000`
（210 分钟）。coordinator 固定在 `FORMAL_START_MS - 1200000`（20 分钟）dispatch
launcher；launcher 实际启动 wall clock 必须落在该 dispatch 的 ±60 秒内，并持续复验
wall clock 相对 monotonic clock 的漂移不超过 1000 ms。

91 次、每 10 秒一次的 load probe 属于独立 disposable campaign，不能作为 formal history
预载。probe 关闭后 launcher 在无 formal WATCH authority 的状态等待，只在 anchor 前的有界
准备窗口生成 fresh admission/policy，并且不得在 `FORMAL_START_MS` 前启动 formal reader、
WATCH generation 或 history segment；错过有界 anchor 则 fail closed。formal warmup 内的
pre-valid append 合法，observer 返回 `WARMUP` 且不增加正式 decision iteration。210 分钟
同一 campaign 新鲜历史覆盖 production 的 5,400 秒 quote span、361 条 raw quote 与 40 根
连续闭合五分钟 bar；不得拼接 probe、旧 campaign 或 authority transition 前的记录。四个
环境变量的名称、值和顺序必须保持精确，不能增加第五个变量；不得使用
`--remain-after-exit`。这个入口只启动 SHADOW/P1，绝不授权 PAPER。

### P1 safety-soak coordinator 与 liveness rehearsal

P1 prospective policy 必须由安装后的固定 executable 生成；输出 schema 是
`hepta.strategy-shadow-observation-policy.v1`，production mode 是
`PRODUCTION_ROOT_PROSPECTIVE_POLICY_PLANNING`：

```bash
sudo /usr/libexec/hepta-p1-safety-soak-policy-planner --run \
  --source-baseline "$SOURCE_BASELINE" \
  --expected-source-baseline-file-sha256 "sha256:$SOURCE_BASELINE_FILE_SHA256" \
  --strategy "$STRATEGY" \
  --runtime-directory "/var/lib/hepta/p1-safety-soak/$CAMPAIGN_ID" \
  --expected-strategy-sha256 "sha256:$STRATEGY_SHA256" \
  --campaign-id "$CAMPAIGN_ID" \
  --launcher-start-ms "$LAUNCHER_START_MS" \
  --output "$POLICY_OUTPUT"
```

经复审的 root-owned launch contract 必须以
`hepta.p1-safety-soak-coordinator-launch-contract.v1` 写入
`/etc/heptatrader/p1-safety-soak/${CAMPAIGN_ID}.json`。四个 safety-soak systemd
对象均为 static、没有 `[Install]`；唯一支持的启动方式是显式启动 target：

```bash
sudo /usr/bin/systemctl start \
  "hepta-p1-safety-soak@${CAMPAIGN_ID}.target"
```

coordinator 发布并持续绑定
`hepta.p1-safety-soak-campaign-runtime.v1`；systemd 只调用安装后的固定 hyphen
executables `hepta-p1-safety-soak-campaign-coordinator`、
`hepta-p1-safety-soak-observer-worker` 与
`hepta-p1-safety-soak-recorder-worker`。fault pins 必须来自安装后的
`hepta-p1-safety-soak-fault-pin-producer`。最终 auditor 调用必须携带
`--campaign-runtime`，不能根据 campaign spec 重建 runtime lineage。target stop 是该组
unit 的显式清理边界；这些对象始终与 PAPER/LIVE units 冲突，且没有 broker/order
authority。

真实 systemd liveness rehearsal 默认不进入 CTest。只有明确 opt in、提供 digest-pinned
base image 与精确 40-hex source commit 时才注册：

```bash
cmake -S . -B build-p1-liveness \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_P1_CAMPAIGN_ROOTFUL_LIVENESS_REHEARSAL=ON \
  -DHEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_IMAGE="$BASE_IMAGE_AT_SHA256" \
  -DHEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT"
ctest --test-dir build-p1-liveness \
  -R '^hepta_p1_campaign_rootful_liveness_rehearsal$' \
  --output-on-failure
```

该 CTest 故意不传 `--certify`；receipt schema 为
`hepta.p1-safety-soak-campaign-rootful-liveness-gate.v1`，mode 固定为
`REHEARSAL_ROOTFUL_NON_CERTIFYING`，因此永远不是 admission/certification evidence。
runner 从 clean source 执行，并把原始 dual-domain BASE runner、review-closure consumer、
四个 production unit 与专用 fixture 精确加入输入 manifest；runner 本身不安装进 runtime。

### Round95 external-certified v3 PAPER admission（历史独立包；非授权）

本节固定的是外部认证 v3 的历史协议与独立安装包。当前 v4
`local-only` 只保留 disabled seed 和非授权 deployment-evidence recorder；active v4
已隔离，不存在可运行的 local-v4 PAPER campaign。下面的 zero-exposure
producer、attestor 与 17-input admission verifier 只有在另行认证的 v3
package、manifest 和 install receipt 明确安装并绑定它们时才存在。
external-certified v3 单次最长 4 小时，因而不能授权 24h campaign。

P1 开始前若 alpha marker 尚不存在，只允许用固定安装的非授权 bootstrap 建立默认
engaged 状态；不得先调用 domain-authority producer，也不得创建 authority manifest、
credential 或 PAPER unit：

```bash
sudo /usr/libexec/hepta-p1-paper-kill-switch-bootstrap --run \
  --expected-paper-uid "$PAPER_UID" \
  --expected-paper-gid "$PAPER_GID" \
  --expected-source-baseline-sha256 "sha256:$SOURCE_BASELINE_SHA256" \
  --expected-installed-sha256 "sha256:$KILL_SWITCH_BOOTSTRAP_SHA256"
```

标准输出 receipt schema 必须是
`hepta.p1-paper-kill-switch-bootstrap-receipt.v1`，operation 必须为
`ENSURE_ENGAGED_NON_AUTHORIZING`、status 为 `COMPLETE`，并精确绑定 alpha、当前 boot、
固定 producer、control directory、内容为 `engaged` 的 marker 与 durable journal。
`authorization_manifest_created`、credential/unit/connector side effect 和
PAPER/LIVE/mutation/direct-broker/order authority 必须全部为 false。bootstrap 只建立 P1
观察所需的安全默认态，绝不能替代 admission。

P1 窗口必须冻结 10–20 个真实交易日、同一时区与 calendar digest，使用 2 分钟真实
cadence 得到至少 200 个 eligible decisions，完整率必须严格大于 99%，且 catch-up 必须
为零。campaign-level `CLOCK_BOOTTIME` 连续时间不得少于 72 个真实小时；任何
authority、audit 或 cleanup failure 都必须为零。短时或 accelerated rehearsal 只能作为
诊断并保持 `NO_GO`，不得生成 PAPER admission candidate。另有同一 boot、
`CLOCK_BOOTTIME`、source/strategy/campaign 的 campaign-level
continuity checkpoints 覆盖从首段前到末段 teardown 后的整个窗口，采样缺口、跨 boot
拼接或 epoch/fence/lease generation 断链都失败关闭。冻结 fault plan 中每个声明故障必须
严格一次执行，具有独立恢复、cleanup 与 zero-residue receipt；最终只能由固定安装的
independent auditor 重验原始 decisions、formal closures、continuity、faults 和 cleanup。

随后从同一 clean frozen round95 lineage 构建 full-P0 release-validation evidence。固定
causal verifier 必须重建 Release/`BUILD_TESTING=ON`、IBAPI off/on、repository/no-Git
四份 8-round soak、完整 CTest、runtime/source/package/delivery closure 与三台独立 native
VM v6 aggregate。外部 retention 不能是 `pending-external` 或 test-local：必须使用系统
固定 `configured-external` trust policy、当前有效的 Ed25519 签名、完整 upload/readback
对象闭包和 indefinite legal hold。只有
`heptatrader.release-validation-closure.v1` 为 GO，且固定
`/usr/libexec/hepta-release-validation-closure-verifier` 因果重建为同一 GO 时才可继续；
该 GO 的 scope 仍只是 `paper-testing-admission-candidate-only`。

五个需要 environment review 的 rootful 认证门必须顺序执行：Agent OS、dual-domain、
inert PAPER-domain、P1 campaign liveness、hard broker-network。每一门各自取得一份新鲜、
有效期不超过 60 分钟的外部 Ed25519 review closure，并在门结束时 secure-reopen；不得
要求或伪造相同 nonce、request、authorization 或 closure 文件。Admission 比较的是各
报告内完全相同的稳定 `environment_fingerprint`：同一 source commit、review key/
reviewer、base、BuildKit/buildx、Docker daemon/boot/namespace 和 enforcing AppArmor
观测及 trust bindings。broker-network v3 仍是单独的直接输入。所有 Docker rootful 门
必须串行；hard-network 的 native nft/netns/cgroup gate 尤其不得和任一 Docker gate
并行，以免 firewall、daemon 或 namespace 观测发生交叉漂移。

P1 GO 后先执行 journaled WATCH-to-PAPER handoff：重验 P1/activation/source，停止并退役
WATCH custodian、reader、Gateway/Simulator 及 activation reconcile timer，保持 marker
engaged、broker deny-all、PAPER units inactive，并发布
`hepta.p1-watch-to-paper-handoff-receipt.v1`。然后 zero-exposure snapshot producer 在固定
host-authority lock 下先创建 gap-free reservation；admission 必须在读取任何其他证据前
取得该 reservation，并连续持锁直到 candidate 发布和 finalization。发布顺序固定为
candidate commit -> finalization tombstone -> current pointer -> reservation owner removal ->
secure reopen。candidate 前后、tombstone 后、pointer 后或 owner removal 后崩溃，都必须
从 durable generation/predecessor/current-pointer 链幂等恢复；不得删除 owner 后重做账户
查询或打开未锁定窗口。

固定 admission verifier 的 17 个直接输入（不得用 aggregate 或人工声明代替）是：

1. frozen source baseline；
2. install manifest；
3. installer receipt v4；
4. current-install pointer；
5. profile receipt；
6. activation receipt；
7. P1 safety-soak audit；
8. full-P0 release-validation closure；
9. Agent OS rootful gate；
10. dual-domain rootful gate；
11. inert PAPER-domain rootful gate；
12. P1 campaign rootful liveness gate；
13. broker-network rootful gate v3；
14. hard broker-network isolation gate；
15. three-native-VM aggregate v6；
16. WATCH-to-PAPER handoff；
17. fresh authoritative signed zero-exposure receipt。

生产调用必须使用固定
`/usr/libexec/hepta-p1-paper-admission-verifier --run`、精确 source-baseline file pin、
上述 17 个路径与 no-replace output；具体 flag 以该冻结 executable 的 `--help` 和 release
manifest 为准，不能从旧 runbook 补猜。该命令只能由真实 root CLI 直接执行，必须保留
`CAP_SYS_ADMIN` 以建立独立 mount namespace；不得放进会移除该 capability 的 systemd
unit，也不得用外部 `chroot`/wrapper/pre-exec hook 代替。因宿主 `/run` 可为 `noexec`，
verifier 会在固定 `/run/hepta/.hepta-release-causal-stage` 上建立私有 `tmpfs`：根层必须
`ro,nosuid,nodev,exec`，唯一可写后代 `/tmp` 必须 `rw,nosuid,nodev,noexec`。它在重验
精确文件集合后固定根目录 FD，再由子进程 `fchdir`/`chroot`/`execve`；路径替换、额外
mount、传播状态、stdlib/ABI/OpenSSL 或证据漂移均须 fail-closed。唯一可接受终态是
`hepta.paper-testing-admission-candidate-receipt.v1`、`status=GO`、
`paper_test_admission_candidate=true`，同时
`paper_authorized/live_authorized/mutation_authorized/direct_broker_access/`
`order_submission_authorized=false`。它仅允许后续独立人员审阅并申请 bounded PAPER
campaign policy，自己不会启动服务、连接 broker 或提交订单。

截至本 runbook 更新时，以上只是已实现的 fail-closed 合同：尚未在 clean frozen round95
上完成真实 rootful 五门、full-P0 外部 retention、三 VM 与 10--20 交易日 P1 运行，也未
产生 fresh 17-input admission GO。当前状态仍是 NO_GO；不得把离线单测、rehearsal 或本文
档升级为 admission evidence。

生产 SHADOW host bootstrap 的唯一 authority-lifecycle 入口是 root-owned
`/usr/libexec/hepta-shadow-watch-custodian` transaction。`hepta-agent-session-bootstrap`
与 `hepta-sessionctl` 是该 transaction 内部使用的低层实现，不是 campaign API。
strategy、observer、collector、exporter 和任何 campaign/controller 代码均不得直接调用
它们，也不得自行 provision、rotate、revoke、删除 token/fence 或清理 export。
custodian 只接受 WATCH，且每份 state、registration、rotation 与 closure 都必须固定
`paper_authorized=false`、`live_authorized=false`、`mutation_authorized=false` 和
`direct_broker_access=false`；这一流程不授权 PAPER 或 LIVE。

root host bootstrap 必须先启动目标只读 reader/controller，并把它的精确 PID 与 UID
交给 custodian；custodian 自行稳定读取并绑定 GID、process start ticks 与 boot ID，
然后创建一个不可复用的 campaign：

```bash
sudo /usr/libexec/hepta-shadow-watch-custodian \
  --domain-config /etc/heptatrader/trust-domains/alpha.json \
  provision \
  --campaign-id eurusd-shadow-20260731-001 \
  --owner-pid '<reader-pid>' \
  --owner-uid '<reader-uid>' \
  --ttl-sec 3600
```

custodian 在调用低层 bootstrap 前先持久化 `PROVISION_PREPARING`，并只在精确
generation 的 supervisor acceptance、root fence、Agent delivery token 与 canonical
lease receipt 全部一致后，原子发布
`/var/lib/hepta-shadow-watch-custodian/alpha/transaction.json` 的
`hepta.shadow-watch-custodian-state.v1`、`phase=ACTIVE` record。
`provision` 返回的 `hepta.shadow-watch-custodian-registration.v1` 必须为
`status=REGISTERED`，且 `campaign_id`、`lease_generation`、
`lease_expires_at_ms`、`state_body_sha256` 与上述 durable `ACTIVE` record 完全相符。
仅看 token 存在、supervisor 返回 `OK`、collector 成功或 socket 可连接都不是
`ACTIVE` 证明。任何非 `ACTIVE`、digest/binding 不一致或不确定结果都必须 fail closed
并由 custodian reconcile/close；在证明 `ACTIVE` 之前，host bootstrap 不得启动
`hepta-shadow-watch-custodian@alpha.service` 的 `supervise`、collector timer 或任何
SHADOW campaign consumer。

`ACTIVE` 证明完成后，root 才可启动 primary custodian supervisor，并显式启用经过复审的
crash/reboot backstop：

```bash
sudo systemctl enable --now \
  hepta-shadow-watch-custodian-reconcile@alpha.timer
sudo systemctl start hepta-shadow-watch-custodian@alpha.service
sudo systemctl start hepta-shadow-watch-collector@alpha.timer
```

`hepta-shadow-watch-custodian@alpha.service` 是正常生命周期 monitor：它绑定 reader
process identity 与 lease expiry，owner 消失、配置漂移、service stop 或 expiry 时进入
close。`hepta-shadow-watch-custodian-reconcile@alpha.timer` 为
`Persistent=true`，只用于 custodian crash/host reboot 后收敛已存在的 durable
transaction；它不是 provision/rotate 入口、不是 collection cadence，也不能恢复或续期
authority。collector timer 仍为 static、`Persistent=false`，只能由该次已复审的 bounded
SHADOW host bootstrap 显式 start；collector 成功后由 systemd `OnSuccess` 触发 exporter，
campaign 代码不直接运行 collector/exporter。

续租只能以当前 `ACTIVE` campaign 和精确 generation 交给同一 custodian：

```bash
sudo /usr/libexec/hepta-shadow-watch-custodian \
  --domain-config /etc/heptatrader/trust-domains/alpha.json \
  rotate \
  --campaign-id eurusd-shadow-20260731-001 \
  --current-generation 1 \
  --ttl-sec 3600
```

rotate 先持久化 `ROTATION_PREPARING`，内部调用低层 bootstrap，并只在新 receipt/fence
链与 `generation=N+1` 全部精确后恢复 `ACTIVE`。不确定结果由 custodian 对账；旧
generation 在新 generation 未明确提交前不得被猜测为已替换，campaign 也不得绕过
custodian 重试低层命令。`rotate` 返回值必须为
`hepta.shadow-watch-custodian-rotation.v1`、`status=ROTATED`，且新的
`lease_generation`、`lease_receipt_body_sha256` 与 `state_body_sha256` 必须和恢复后的
durable `ACTIVE` record 一致，consumer 才可继续。

正常结束先停止新的 collection，再由 custodian close；禁止手工 `rm` runtime 文件：

```bash
sudo systemctl stop hepta-shadow-watch-collector@alpha.timer
sudo systemctl stop hepta-shadow-watch-collector@alpha.service
sudo systemctl stop hepta-shadow-watch-export@alpha.service
sudo /usr/libexec/hepta-shadow-watch-custodian \
  --domain-config /etc/heptatrader/trust-domains/alpha.json \
  close --reason operator-request
```

close 必须先持久化 `CLOSING`，隔离 Agent token，并以 root fence 撤销 state 中的精确
generation。若 fence 已丢失，只能保持隔离并等待 receipt 的准确 expiry，不能把未知
authority 当成已关闭。只有 authoritative outcome 为 `ACCEPTED`、
`ALREADY_ABSENT`（精确 generation 已权威不存在）或 `EXPIRED`，且以下 residue 全部为零，
才允许完成 closure：

- `/run/hepta-agent-alpha/sessions/session.token`、
  `/run/hepta-agent-alpha/sessions/.session-fence.token` 与
  `/run/hepta-agent-alpha/sessions/shadow-watch-lease-receipt.json` 均不存在；
- `/run/hepta-shadow-watch-export-alpha` 整个目录不存在，因此 `snapshot.json`、
  `shadow-watch-lease-receipt.json` 与 `shadow-watch-export-receipt.json` 均为零；
- active
  `/var/lib/hepta-shadow-watch-custodian/alpha/transaction.json` 已删除；
- root-owned mode `0600`
  `/var/lib/hepta-shadow-watch-custodian/alpha/closures/eurusd-shadow-20260731-001.json`
  存在，`schema=hepta.shadow-watch-custodian-closure.v1`，且
  `campaign_id`、`lease_generation`、`lease_receipt_body_sha256`、
  `fence_token_sha256` 与 `authoritative_revoke_outcome` 均绑定本次精确 closure；
  `local_authority_removed=true`、`export_evidence_removed=true`，并且
  `paper_authorized=false`、`live_authorized=false`、
  `mutation_authorized=false`、`direct_broker_access=false`。

只看到 service inactive、命令 exit 0、token 不存在或 lease 到期都不构成 closure。
closure receipt 和零 residue 必须同时验证后，才可停用该 domain 的 reconcile timer。
若 close 返回 `PENDING_EXPIRY`，必须保持 timer/隔离并等待其收敛，不能启动新 campaign。

revoke/expiry 采用持久两阶段 owner fence：lease 先写入 `fence_pending`，本地 session
立即 disabled；只有 Execution 接受 fence 后才删除 lease/catalog。远端不可用时操作
必须失败且同 owner 不能 provision/renew/rotate 绕过。Gateway 每秒重试；重启时若仍
无法 fence pending/expired/WATCH-active record，Gateway 必须拒绝启动，不能把它恢复为
enabled。

tool socket 位于 `/run/hepta-agent/tools.sock`，由 systemd 以
`hepta-agent:hepta-agent`、mode `0600` 创建；supervisor socket 保持在
gateway-owned、mode `0700` 的 `/run/hepta-tool-gateway` 中且 socket mode `0600`。
Gateway 仅通过具名 socket activation FD 继承两条监听通道，不依赖在 root-owned Agent
目录中新建文件。请求仍须同时通过 `SO_PEERCRED` 与 session token 校验。Codex/OpenClaw
宿主服务必须直接以固定 `hepta-agent` UID/GID 2004 启动 MCP child；本 plugin 不执行
身份切换，普通登录用户或 root 直接加载会因 UID contract fail closed。

Gateway 生命周期必须按 socket ownership 理解：

- `systemctl restart hepta-tool-gateway.service` 只重启 service；两个 socket unit 持续
  active，两个 socket inode 必须保持不变。
- `systemctl stop hepta-tool-gateway.service` 不是完整 shutdown。socket 仍 active，下一次
  连接会重新拉起 service；不要用这条命令证明 Agent endpoint 已关闭。
- 完整 shutdown 必须在同一维护动作中停掉 service 和两个 socket unit：

  ```bash
  systemctl stop hepta-tool-gateway.service \
    hepta-tool-gateway.socket hepta-tool-session-supervisor.socket
  ```

  完成后 `/run/hepta-agent/tools.sock` 与
  `/run/hepta-tool-gateway/session-supervisor.sock` 必须都不存在。
  `RuntimeDirectoryPreserve=yes` 允许空的 private runtime 目录继续存在；空目录不是
  Gateway 存活或 endpoint 可连接的证据。恢复 socket activation 时显式执行：

  ```bash
  systemctl start hepta-tool-gateway.socket \
    hepta-tool-session-supervisor.socket
  ```

源码 checkout 可用唯一命名、临时 user-systemd unit 复验上述语义；该动态门默认跳过，
必须显式 opt-in，且不会启动生产 Hepta unit：

```bash
python3 scripts/run_hepta_agent_os_systemd_lifecycle_gate.py --run --require
```

常规离线矩阵不注册任何 host-runtime 门。复审环境可在独立 build tree 中显式加入两门：

```bash
cmake -S . -B build-agent-os-runtime-gates \
  -DHEPTA_ENABLE_AGENT_OS_USER_SYSTEMD_LIFECYCLE_GATE=ON \
  -DHEPTA_ENABLE_OPENCLAW_REAL_LOADER_GATE=ON
ctest --test-dir build-agent-os-runtime-gates --output-on-failure \
  -L explicit-rehearsal
```

完整四 UID Agent OS 生命周期使用另一个默认关闭的 disposable rootful-systemd
E2E 门。它只接受 fresh Release、`BUILD_TESTING=ON`、
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`、`HEPTA_ENABLE_IBAPI=OFF` 的 build；只使用本机
预加载、digest-pinned 且已复审的 systemd base，不 pull、不联网、不 bind mount
宿主路径、不安装 IB adapter 或 PAPER/LIVE unit。build tree 必须位于同一源码
checkout 内，仓库外 build 会 fail closed。容器必须使用专用
`hepta-systemd-gate` AppArmor profile，`network=none`、private cgroup namespace、
read-only rootfs 和固定 tmpfs allowlist；不得降级到 `privileged` 或 `unconfined`。
除 reviewed base 的独立 GO JSON 外，每次运行还必须提供独立 SHA-256 绑定的
AppArmor GO JSON。后者精确绑定 `hepta-systemd-gate`、reviewed policy source
SHA-256、loaded profile SHA-256、raw policy SHA-256 与 raw ABI；development
candidate base 也不能绕过该门。第三份独立 SHA-256 绑定的 GO JSON 必须把
Docker Engine ID、root-owned daemon PID、该进程的 `/proc` start-time ticks
及 boot ID 绑定到同一个 AppArmor root/unstacked namespace；runner 会在 Docker
API 前后复验该进程实例，不能仅凭 socket 路径或 profile 名假设 namespace 相同。
另有独立 isolated-builder GO 绑定 exact BuildKit RepoDigest、buildx binary/version
和 Docker server semantics；这些输入彼此不能替代。
容器内固定验证 UID/GID 2001 Gateway、2002 Simulator、2003 reserved IB execution、
2004 Agent 的隔离，custodian-owned WATCH provision/rotate/close、`ACTIVE` handoff、
crash reconcile、closure receipt、socket activation、service-only
restart/reactivation 和完整 socket shutdown。它仍只是共享宿主 kernel 的
`offline-disposable-container-rehearsal`，不能代替三台 native disposable VM 门。
门禁不再读取 legacy profile 名称列表。它先锚定固定
`/sys/kernel/security/apparmor/policy`：上层必须是 root-owned securityfs，
`policy` 必须是 root-owned `apparmorfs:[id]` magic link，打开后的 filesystem
magic 必须是 AAFS `0x5a3c69f0`，namespace 必须是 `root`、level 0、unstacked。
随后从该打开的 AAFS descriptor 按 `name`
精确唯一定位 profile，要求 `mode=enforce`、`attach=hepta-systemd-gate`、
`learning_count=0`，并验证 profile/raw digest、raw ABI、raw-data symlink/目录
绑定；执行前后完整记录必须相同。该内容证明仍只对运行时已加载 policy 与外部
reviewed GO 的一致性负责，不会自行创建、加载或复审 policy source。

手工入口：

```bash
python3 scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py \
  --build-dir build-round35-v6-ibapi-off-release \
  --base-image '<reviewed-agent-os-base>@sha256:<64-lowercase-hex>' \
  --buildkit-image '<reviewed-buildkit>@sha256:<64-lowercase-hex>' \
  --reviewed-base-provenance /absolute/path/base-go.json \
  --reviewed-base-provenance-sha256 sha256:<64-lowercase-hex> \
  --reviewed-builder-provenance /absolute/path/builder-go.json \
  --reviewed-builder-provenance-sha256 sha256:<64-lowercase-hex> \
  --buildx-binary-sha256 sha256:<64-lowercase-hex> \
  --apparmor-provenance /absolute/path/apparmor-go.json \
  --apparmor-provenance-sha256 sha256:<64-lowercase-hex> \
  --docker-apparmor-namespace-provenance \
    /absolute/path/docker-apparmor-namespace-go.json \
  --docker-apparmor-namespace-provenance-sha256 sha256:<64-lowercase-hex> \
  --report \
    build-round35-v6-ibapi-off-release/agent-os-rootful-systemd-e2e-gate.json
```

CTest opt-in 入口：

```bash
cmake -S . -B build-agent-os-rootful-systemd \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_AGENT_OS_ROOTFUL_SYSTEMD_E2E_GATE=ON \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_IMAGE=\
'<reviewed-agent-os-base>@sha256:<64-lowercase-hex>' \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE=/absolute/path/base-go.json \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256=\
sha256:<64-lowercase-hex> \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDKIT_IMAGE=\
'<reviewed-buildkit>@sha256:<64-lowercase-hex>' \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE=\
/absolute/path/builder-go.json \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256=\
sha256:<64-lowercase-hex> \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDX_BINARY_SHA256=\
sha256:<64-lowercase-hex> \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE=\
/absolute/path/apparmor-go.json \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256=\
sha256:<64-lowercase-hex> \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE=\
/absolute/path/docker-apparmor-namespace-go.json \
  -DHEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256=\
sha256:<64-lowercase-hex>
ctest --test-dir build-agent-os-rootful-systemd --output-on-failure \
  -R '^hepta_agent_os_rootful_systemd_e2e_gate$'
```

安装组件同时提供
`/usr/share/heptatrader/.agents/plugins/marketplace.json` 与
`/usr/share/heptatrader/plugins/heptatrader-agent-os`。Codex 必须先把前者所在的
`/usr/share/heptatrader` 注册为 marketplace，再安装版本化 entry；OpenClaw 使用
`openclaw plugins install` 安装同一 Codex-compatible bundle：

```bash
codex plugin marketplace add /usr/share/heptatrader
codex plugin add heptatrader-agent-os@heptatrader
openclaw plugins install /usr/share/heptatrader/plugins/heptatrader-agent-os
```

注册或安装 plugin 不创建 OS identity、runtime 目录或 session，也不授权 PAPER/LIVE。
但 OpenClaw 2026.7.1-2 的 `plugins install` 会写入 `enabled=true`，不是 disabled
staging；只应在完成 source/policy review 后运行，并用 `plugins inspect --runtime
--json` 复验 `explicitlyEnabled=true`、`activated=true` 及唯一的 `heptatrader` stdio
MCP。源码 checkout 的隔离 real-loader 门为
`python3 scripts/run_heptatrader_openclaw_loader_gate.py --run --require`。
Codex/OpenClaw host 仍必须按上述 drop-in 以 UID/GID 2004 运行，并在新会话中加载 MCP。
MCP server 是常驻 stdio 进程，直接编码 `hepta.agent-tools` Unix 帧；每次 tool call
建立一条短 Unix 连接，不 fork `heptactl`，也不接受 argv/env 明文 token。默认只读取
严格 `0600` 的 `/run/hepta-agent/session.token`。

Round24 起 combined soak schema 升级为 `hepta.execution-gateway-soak.v8`。当时的协议在原有
daemon identity pair 上新增 `ReadAuthoritativeState`，Gateway 的 quote/account/positions/
orders/risk reads 必须由 Execution Service 返回，不能回退到本地缓存。每轮还必须证明
真实 Tool socket 上的 authoritative read、remote mutation 与 owner fence；v7 报告只能
作为 Round23 历史证据，不能证明当前只读权威边界。

Round25 起 schema 升级为 `hepta.execution-gateway-soak.v9`，新增 IB child ready 后的
`GetServiceIdentity` 有界握手，证明首次业务请求不会抢在 execution accept/readiness
线程真正可服务之前。fixture 初始化订单还必须使用同一 `tool_call_id` 做有界幂等重试，
并以 broker ledger 精确一次 send 证明重试没有制造重复下单。v8 只能证明 Round24
authoritative read，不证明该 anti-flake gate。

Round34 起，IB PAPER 的权威行情订阅属于 Execution Service，不能由 Agent 或 Gateway
传入原始 IB contract/request id。部署前必须在 root-owned、mode `0644` 的
`/etc/heptatrader/hepta-execution-ib-paper.env` 中配置：

```text
HEPTA_IB_PAPER_QUOTE_CONTRACTS=EUR.USD|EUR|CASH|IDEALPRO|USD
HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT=EUR.USD
HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=5000
```

当前正式 contract 仅接受 1–64 个精确
`instrument|symbol|CASH|exchange|currency` 记录，instrument 必须等于
`symbol.currency`。daemon 启动时由 Execution 发起并记录每个 `ReqMktData`，只有完整
bid/ask 后才进入 ready；停止时由同一 Execution 撤销 `CancelMktData`。未知、未就绪或
非有限、非正、bid/ask 交叉、instrument/generation 不匹配或超过
`QUOTE_MAX_AGE_MS` 的 quote 一律 fail closed，`risk.preview_order` 与
`trade.place_order` 不得回退到 Gateway 缓存。这些字段只建立 broker-owned 行情契约，
不授权 PAPER/LIVE；PAPER 仍要求独立 authorization credential、kill switch、risk、
OMS/reconcile 及显式运维授权全部通过。

当前 combined soak schema 为 `hepta.execution-gateway-soak.v11`。每轮运行 9 个
内容寻址的独立二进制，除 v9 的 Execution/Gateway/event/IB crash-replay 边界外，还必须
精确证明：

- preview permit 由 Execution Service 签发、最长 5 秒且单次使用；
- future mutation `command_id` 由 Execution 签发，MCP 原样映射并以同一 ID 重试；
- owner fence 会撤销该 owner 的所有未消费 permit；
- durable Accepted 的相同 command 重放返回成功 duplicate，且 venue send 精确一次；
- Simulator quote 由 Execution-owned periodic worker 刷新，原始 TTL 过去后真实 Unix
  read 与 preview 仍只在新 observation 存在时成功；
- standalone Gateway 报告真实 Execution liveness；
- IB fake-broker Agent-tool 流程覆盖 quote、preview、place、同 ID replay 与 cancel，
  `flatten` 在没有 atomic reduce-only authority 时保持不暴露。
- IB authoritative event/adapter 边界必须证明实际 broker contract 与 Execution
  配置的完整合约逐字段精确绑定；任何漂移均在最终发送前 fail closed 且零 broker send。

推荐命令：

```bash
python3 scripts/run_execution_gateway_soak.py \
  --build-dir build-current-ibapi-off-release \
  --rounds 8 \
  --require-build-type Release \
  --report build-current-ibapi-off-release/execution-boundary-soak-v11-ibapi-off-8-final.json
```

`--report` 必须是已验证 build directory 的直接普通文件子项；完成后再由
evidence index/closure 工具内容寻址地封存或外置，不能让运行中的 soak 跨信任根直接
写入 `runtime-logs/`。

该报告仍是离线 code/process certificate，不是 provisioned-host、三 VM、PAPER 或 LIVE
授权。IBAPI-on 必须在独立 Release build 中重复同一 8×9 套件。

## 0. 历史记录：Round18 独立执行边界 checkpoint（当前认证禁用）

以下命令仅保留为 Round18 的历史证据说明，不得用于当前认证。Round18 当时要求
canonical split Execution Service 在任何 rootful systemd 或 PAPER 验证前，从 fresh
Release build 生成六套件 Version 5 离线证据。当前 runner 已是上文定义的 v11/九套件
合同；当前操作必须使用上文 v11 命令，不得复用下列 v5 报告名或验收条件。

```bash
cmake -S . -B build-agent-os-round18-verify \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF
cmake --build build-agent-os-round18-verify \
  --target hepta_agent_os_test_binaries --parallel 2
ctest --test-dir build-agent-os-round18-verify \
  --output-on-failure --parallel 2
python3 scripts/run_execution_gateway_soak.py \
  --build-dir build-agent-os-round18-verify \
  --rounds 32 \
  --require-build-type Release \
  --report build-agent-os-round18-verify/execution-boundary-soak-round18-v5-32.json
```

该历史报告当时必须为 `hepta.execution-gateway-soak.v5`、六套件全部完成，并逐轮满足
当时的精确 machine-evidence 合同。Round18 新增的关键证据是旧 event identity backlog 拒绝、
mutation/event 双 socket identity 一致性、event restart 后显式 identity refresh，以及
identity 拒绝时无 source read、无 cursor advance、无 local publish。Gateway 还必须
证明双 socket 已验证 pair 会原样固定到 dispatch、同 owner identity/cursor 操作串行，
且 resync control 只接受精确 type/venue/reason 合同。

identity mismatch 后不得在同次调用自动刷新重试，也不得手工清 cursor 或 resync
latch。确认两个 socket 报告相同 `{serviceEpoch, serviceFencingGeneration}` 后，必须
通过 execution control socket 发起 service-owned authoritative reconcile；只有返回
`Accepted`、`mutationBlocked=false` 且仍匹配当前 identity 时，Gateway 才可确认并
解除该 owner 的 resync latch。

该报告仅闭合离线边界，不授权启动/启用 systemd unit，不授权连接 TWS/Gateway，
也不授权 PAPER/LIVE。下一道独立门仍是 disposable provisioned host 上的 rootful
effective-systemd、credential mount、socket inode、stop cleanup 与网络隔离验证。

## 0.1 Round98 disposable effective-systemd 门

当前 round105 把该门保持为两层。`check_hepta_execution_provisioned_host.py` 仍是完全离线、
不启动服务的静态第一层；它现在还必须检查 Simulator env/fence、canonical tmpfiles
声明，以及两个 root-owned `0755` single-link ELF。synthetic fixture 只证明 checker
fail-closed，不能替代真实 NSS、mount namespace、cgroup 或 activated socket 证据。

第二层是显式 opt-in 的 Docker rehearsal，默认 CTest 不注册。它只允许在一次性宿主
运行，并硬性要求：

- root-owned `0400` sentinel
  `/etc/heptatrader/hepta-rootful-systemd-gate.disposable`，内容为精确四行：
  `HEPTA_DISPOSABLE_ROOTFUL_GATE_V1`、当前 `/etc/machine-id`、当前 boot ID，
  以及已经运行的本机 Docker daemon ID；该文件只能由一次性宿主 provisioner 创建，
  reboot 或替换 Docker daemon 后旧 sentinel 必须失效；
- 预加载、digest-pinned 且带
  `io.hepta.rootful-systemd-base.version=1` 和
  `io.hepta.rootful-systemd-base.offline-ready=true` 标签的 reviewed gate base；
- host 已加载名为 `hepta-systemd-gate` 的专用 enforcing AppArmor profile；不得使用
  `unconfined`；
- Linux amd64、systemd cgroup driver、cgroup v2、builtin seccomp 和 private cgroup
  namespace。

rehearsal 的 image build 使用 `--network=none`，runtime 使用 `--network=none`、只读
rootfs、精确 tmpfs/capability allowlist；不允许 `--privileged`、host PID/cgroup/network、
端口发布、Docker socket或任何用户配置的 host bind mount。`real` variant 只执行真实
Simulator ELF，前后证明 IB service 从未启动；`sandbox` 和 `stub` variant 在 immutable
image build 中先替换 canonical IB path，分别验证 systemd IP policy/credential/kill
switch/cgroup cleanup，以及 IB-disabled adapter fail-closed。真实 IBAPI ELF仅由 outer
runner 通过 no-follow descriptor 读取并 hash，字节不进入 build context/image，更不会
执行。client probe 只有 identity 和 read-only event wait，mutation count 必须为零。

必须从 `umask 0027`（或更严格）开始生成两棵 fresh build；build/report 目录若 group 或
world writable，runner 会在任何 Docker API 前拒绝。示例：

```bash
umask 0027
install -d -m 0750 runtime-logs
cmake -S . -B build-round105-release \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF
cmake --build build-round105-release \
  --target hepta_agent_os_test_binaries --parallel 2

cmake -S . -B build-round105-ibapi \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DHEPTA_ENABLE_IBAPI=ON \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF \
  -DIBAPI_ROOT="$IBAPI_ROOT"
cmake --build build-round105-ibapi \
  --target hepta_agent_os_test_binaries --parallel 2
```

手工调用模板仅可在已满足上述前置条件的一次性 VM 内使用：

```bash
python3 scripts/run_hepta_execution_rootful_systemd_gate.py \
  --ibapi-build-dir build-round105-ibapi \
  --ib-disabled-build-dir build-round105-release \
  --base-image '<reviewed-gate-base>@sha256:<64-lowercase-hex>' \
  --report runtime-logs/execution-rootful-systemd-round105-rehearsal.json
```

CTest opt-in 等价入口只可在同一 disposable host 上配置；普通 PR/CTest 必须保持 OFF：

```bash
cmake -S . -B build-round105-ibapi \
  -DHEPTA_ENABLE_CONTAINERIZED_SYSTEMD_REHEARSAL=ON \
  -DHEPTA_REHEARSAL_IB_DISABLED_BUILD_DIR="$PWD/build-round105-release" \
  -DHEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE=
'<reviewed-gate-base>@sha256:<64-lowercase-hex>'
ctest --test-dir build-round105-ibapi \
  -R '^hepta_execution_containerized_systemd_rehearsal_tests$' \
  --output-on-failure
```

即使 rehearsal 为绿，也只签发
`containerized-effective-systemd-rehearsal`：Docker 共享宿主 kernel，当前报告明确不
声称 AppArmor policy 内容 attestation，也不能替代 native disposable-VM/rootful
effective-systemd 最终门。缺少 sentinel、专用 profile 或 reviewed base 时必须在任何
container/service 启动前失败；不得在日常工作站补 sentinel，也不得降级到
`privileged`/`unconfined`。最终 native 门通过前，不得连接真实 TWS/Gateway、不得执行
真实 IBAPI daemon、不得开启 PAPER/LIVE。

失败报告的 `failure_stage` 是单调外层进度，不是认证结果。仅当
`container_start_attempted=false` 时，报告才会把 IBAPI 执行、broker 连接、PAPER order
和 LIVE enable 精确记录为 `false/0`；一旦尝试创建容器而内层证据尚未完整返回，这些
运行时边界必须保持 `unknown`。`docker_api_touched`、`image_build_started` 和
`completed_variants` 用于区分纯本地前置失败、Docker 只读预检失败、image build 失败与
部分 variant 完成，不能把 blocked rehearsal 解释为 PASS。

## 0.2 当前 native disposable-VM Agent OS WATCH runtime 门

当前代码门由四部分组成：

本节 schema 角色的 machine-readable 精确映射如下；键、值和角色不得互换或扩展：

```json
{
  "bundle": "hepta.execution-native-vm-bundle.v7",
  "verification": "hepta.execution-native-vm-bundle-verification.v7",
  "provisioning": "hepta.execution-native-vm-provisioning-manifest.v6",
  "image": "hepta.execution-native-vm-image-manifest.v4",
  "runtime_variant": "hepta.execution-native-systemd-gate.v6",
  "runtime_aggregate": "hepta.execution-native-systemd-aggregate.v6"
}
```

- `build_hepta_execution_native_vm_bundle.py` 从一组 IBAPI-on/off Release build
  和一份已完整复验的 deterministic clean-source tar/manifest
  确定性生成 `real`、`sandbox` 或 `stub` 的 broker-free rootfs tar、provisioning
  manifest、image manifest 与 bundle 报告。四者的 schema 必须精确等于上方
  machine-readable 映射中的 `bundle`、`verification`、`provisioning` 与 `image`；
  产物还必须包含
  `hepta-tool-gatewayd`、`hepta-sessionctl`、`heptactl`、MCP server/UID launcher、
  WATCH-only custodian/bootstrap、plugin、systemd sockets/service、tmpfiles、四 UID
  identity manifest、非 secret env、Agent OS installation preflight、原样复用的
  `hepta_agent_os_rootful_inner_gate.py`，以及
  `agent-os-runtime-input-manifest.json`。两套 build 必须从同一个、无 `.git`、无额外
  文件且内置 `.hepta/source-bundle-manifest.json` 与外部 manifest 逐字节相同的
  clean-source 解包树配置；普通 worktree build、跨 bundle build 和 source 内 build
  一律拒绝。bundle 还内置外部 source manifest、两套原始 CMakeCache/
  compile_commands 和 canonical source/build lineage，把所有 staged repository
  source 的路径/mode/bytes 及每个 staged ELF 绑定到对应 build。bundle 只声明
  runtime required 并绑定输入；
  不得在构建时写入 socket、session token、runtime sentinel 或 runtime PASS。
- `verify_hepta_execution_native_vm_bundle.py` 独立复验 tar 的 root:root/mtime/mode、
  文件和目录精确闭包、三份 manifest 绑定、formal ELF 缺席、variant canonical IB
  映射、clean-source provenance 精确绑定、Agent OS 静态安装文件精确闭包，以及零连接、
  零订单、PAPER/LIVE=false 边界。verifier 必须再次接收并独立复验同一份 external
  clean-source tar/manifest；只验证 bundle 自述的 lineage 不构成 PASS。
- `run_hepta_execution_native_systemd_gate.py` 只在一台预先 provision 的 native VM 内
  运行一个 `real`、`sandbox` 或 `stub` variant；它不创建用户、凭据、sentinel，不安装
  unit，不修改网络，也不启动 Docker。runner 还必须接收外部 provisioner/hypervisor
  Ed25519 签名的单次 instance receipt；receipt 绑定随机 challenge、instance UUID、
  provisioner/hypervisor ID、variant、boot/run/image/provisioning/source lineage 和短期
  有效窗口，并由固定 production trust policy 安全重开复验。报告 schema 必须精确等于
  上方 machine-readable 映射中的 `runtime_variant`。
- `aggregate_hepta_execution_native_systemd_gate.py` 只读取三份 root-owned `0600`
  variant 报告，并要求三组不同的 machine/boot/run/image identity、相同的 VM 类型、
  kernel、platform policy、clean-source bundle/manifest/files digest、Agent OS
  installation manifest/Gateway/sessionctl/MCP hash、Simulator、client probe 与 formal
  IBAPI hash、每台 VM 的 runtime input/result/lifecycle digest，以及精确的
  real/sandbox/stub broker-free executed-binary closure。输出 schema 必须精确等于上方
  machine-readable 映射中的 `runtime_aggregate`；v6 聚合器还会重新验证三份外部
  instance receipt，并要求 UUID/challenge 三者互异；它拒绝旧的 v5 报告。

每个 variant 必须使用独立、一次性、native VM；PID 1 必须是 systemd，PID 1 必须位于
cgroup v2 根，不得暴露 Docker socket。执行 gate 前必须物理断开/禁用所有非 loopback
网络：`lo` 必须只有 `127.0.0.1` 和 `::1`，其他 link 必须 `DOWN`，不得有非 loopback
地址、route 或 default route。三台 VM 不得复用 machine ID、boot ID、run ID 或 image
manifest digest。

`vm_image_manifest_sha256` 是 image 内“除 image manifest 自身及其 digest 文件外”的
相关 immutable 文件清单 SHA-256，不是 QCOW/raw/AMI 整体哈希。将整体 VM image 哈希写回
该 image 会形成不可满足的自引用，因此禁止用整体 image digest 替代此字段。外层 image
builder 仍应单独记录最终 QCOW/raw/AMI digest，但它不参与 sentinel 的自引用契约。

在离线构建机上为三个 variant 分别执行（输出目录必须尚不存在）：

```bash
scripts/build_hepta_execution_native_vm_bundle.py \
  --variant real \
  --ibapi-build-dir build-round105-ibapi \
  --ib-disabled-build-dir build-round105-release \
  --platform-policy tests/native_systemd/platform-policy-v1.json \
  --clean-source-bundle runtime-logs/round105/heptatrader-clean-source-round105-a.tar \
  --clean-source-manifest runtime-logs/round105/heptatrader-clean-source-round105-a.manifest.json \
  --output-dir runtime-logs/native-vm-bundles-round105-real

scripts/verify_hepta_execution_native_vm_bundle.py \
  --bundle-report runtime-logs/native-vm-bundles-round105-real/hepta-native-vm-real.bundle.json \
  --archive runtime-logs/native-vm-bundles-round105-real/hepta-native-vm-real.rootfs.tar \
  --clean-source-bundle runtime-logs/round105/heptatrader-clean-source-round105-a.tar \
  --clean-source-manifest runtime-logs/round105/heptatrader-clean-source-round105-a.manifest.json \
  --report runtime-logs/native-vm-bundles-round105-real/hepta-native-vm-real.verification.json
```

`sandbox`、`stub` 必须使用各自的新输出目录并替换两处 variant。只有独立 verifier PASS
的 tar 才能交给已复审的 image builder。image builder 必须将 tar 应用到空白、digest-pinned、
offline-ready systemd base，创建固定 UID 2001/2002/2003/2004，永久移除/禁用非 loopback 网络，
复验 AppArmor 与文件 ownership/mode，然后才可在首次启动时注入唯一 machine ID、boot ID、
run ID 和下述 sentinel。bundle 本身不得包含这些 instance identity，也不得包含 formal IB ELF。

VM provisioner 必须创建 root-owned `0400` single-link 文件
`/etc/heptatrader/hepta-native-systemd-gate.disposable`，精确十二行：

```text
HEPTA_DISPOSABLE_NATIVE_SYSTEMD_GATE_V1
machine_id=<32 lowercase hex>
boot_id=<UUID lowercase hex>
vm_image_manifest_sha256=<64 lowercase hex>
provisioning_manifest_sha256=<64 lowercase hex>
platform_policy_sha256=<64 lowercase hex>
clean_source_bundle_sha256=<64 lowercase hex>
clean_source_manifest_sha256=<64 lowercase hex>
clean_source_files_sha256=<64 lowercase hex>
variant=<real|sandbox|stub>
run_id=<32 lowercase hex>
instance_challenge=<64 lowercase hex>
```

同一 image 内还必须预置 root-owned 输入：两个 runner、Execution inner v3、Agent OS
四 UID inner、Execution 与 Agent OS 两个 provisioned-host preflight 为 `0755`；
variant/image/Agent OS installation/runtime-input metadata 为 `0444`；Execution
`/run` inner sentinel 为 `0400` 且内容等于 `run_id`。runner 首先以
`--installation-only` 验证不可授权 placeholder 且不存在 tool socket、session token、
supervisor socket；此时只能记录 `runtime_preflight_executed=false`。只有随后在同一
真实 native VM 内实际执行并严格解析四 UID inner 的结果，才可将该字段升级为 `true`。
严禁用普通文件、伪 socket/token、手工布尔值或复制的 container result 冒充 runtime
证据。canonical IB path 在
`real`/`stub` image 中必须是
IB-disabled stub，在 `sandbox` image 中必须是 broker-free sandbox probe；formal IBAPI ELF
只允许以 SHA-256 进入 metadata，不得进入可执行 image。

每台 VM 内的调用模板：

```bash
install -d -o root -g root -m 0750 /var/lib/hepta-gate-evidence
/usr/local/libexec/run_hepta_execution_native_systemd_gate.py \
  --variant real \
  --instance-receipt \
    /var/lib/hepta-gate-evidence/execution-native-systemd-real-instance-receipt.json \
  --report /var/lib/hepta-gate-evidence/execution-native-systemd-real.json
```

另外两台分别替换为 `sandbox` 和 `stub`。将三份报告复制到一台仍为离线、root-only 的
证据 VM 后执行：

```bash
/usr/local/libexec/aggregate_hepta_execution_native_systemd_gate.py \
  --real-report /evidence/execution-native-systemd-real.json \
  --sandbox-report /evidence/execution-native-systemd-sandbox.json \
  --stub-report /evidence/execution-native-systemd-stub.json \
  --report /evidence/execution-native-systemd-aggregate.json
```

单份 PASS 只签发
`native-disposable-vm-agent-os-watch-runtime-systemd-variant`。runner 在离线、一次性
native VM 上创建临时 `/etc/heptatrader` tmpfs 与随机 lease-store key，将 broker-facing
canonical path 在 WATCH lifecycle 期间 fail-closed 隔离，执行四 UID inner，并在最终
报告前由 custodian 精确关闭 generation、证明 token/fence/export 零 residue 与 closure
receipt、卸载 tmpfs、恢复原 inode，再复验全部输入稳定。
任何 cleanup 或恢复不完整都不得产生 PASS。只有三份独立报告通过 v6 聚合，才签发
`native-disposable-vm-agent-os-watch-runtime-rootful-systemd`；该级别明确
`agent_os_runtime_preflight_executed=true`、`paper_authorized=false`、broker connections
和 orders 均为 0。runtime 模式不是 metadata 检查：它要求 root 在真实宿主根执行，并
清空 supplementary groups、降权到固定 UID/GID 2004 启动已安装 MCP launcher；同一
stdio 会话必须完成 `initialize`、`tools/list`，
发现精确 11 个 WATCH tools，并严格执行下方 machine-readable 数组绑定的全部 read
调用；该数组是唯一的调用顺序与参数契约。发现列表必须完全不含 `trade.*` 与
`risk.preview_order`，响应的 MCP text/structured envelope 必须逐字义一致。health
同时证明 token/session authentication、tool socket traversal、Gateway ready 与
`remote_execution_ready=true` 的 Simulator identity。死 socket、伪 token、未登记
session、Execution down、mutation tool 泄漏或任一 read 失败均必须失败。上述 runtime
证据及 PAPER venue certification 都必须再次单独、显式授权。日常工作站不得创建 native
sentinel，也不得把容器 rehearsal 报告混入 native 聚合。

上述 runtime read probes 的 machine-readable 精确顺序和参数如下；不得重排、遗漏或追加：

```json
[
  {"tool": "system.get_health", "arguments": {}},
  {"tool": "market.get_quote", "arguments": {"instrument": "EUR.USD"}},
  {"tool": "account.get_summary", "arguments": {}},
  {"tool": "portfolio.list_positions", "arguments": {}},
  {"tool": "orders.list", "arguments": {}},
  {"tool": "risk.get_limits", "arguments": {}},
  {"tool": "watch.get_snapshot", "arguments": {"instrument": "EUR.USD"}}
]
```

## 本地 AI PAPER：DENY_ALL 下的被动部署

PAPER runtime 的部署不授予交易权限。部署前必须已有旧 campaign 的 terminal end-flat
receipt（首次安装除外），并保持 broker egress 为精确 `DENY_ALL`、授权 connector 为 0、
agent/Execution/Gateway/operator 和所有 PAPER timer 均不活动。若旧 campaign 尚未终结，
先幂等执行：

```bash
sudo /usr/libexec/hepta-local-paper-repair end-flat
sudo /usr/libexec/hepta-local-paper-control status --domain alpha
sudo /usr/libexec/hepta-broker-egress-policy \
  --policy /usr/share/heptatrader/hepta-broker-network-policy-v1.json \
  --identity-manifest /usr/share/heptatrader/hepta-service-identities-v1.json \
  --check-deny-all
```

`hepta-local-paper-control status` 的 JSON 必须精确表示 `mode=DENY_ALL`、`paper_authorized=false`、
`live_authorized=false`、`identity_count=0`；egress checker 必须报告
`authorized_connectors=0`、空 `authorized_uids` 和 `protected_ports=4`。部署期间不得启动
IB PAPER、Gateway 或 agent。先将两个被动组件安装到全新的 DESTDIR，记录 staging 中每个
文件的 digest，并运行 install-tree/unit/source gate；不能直接把一个未经验证的 build tree
覆盖到宿主：

```bash
BUILD_DIR="$PWD/build-paper-release-ibapi"
STAGE="$(mktemp -d /var/tmp/hepta-paper-stage.XXXXXX)"
STAGE_HASHES="$(mktemp /var/tmp/hepta-paper-stage-sha256.XXXXXX)"

DESTDIR="$STAGE" cmake --install "$BUILD_DIR" --prefix /usr \
  --component hepta-execution-runtime
DESTDIR="$STAGE" cmake --install "$BUILD_DIR" --prefix /usr \
  --component hepta-agent-os-runtime
(cd "$STAGE" && find usr -type f -print0 | sort -z | xargs -0 sha256sum) \
  >"$STAGE_HASHES"

python3 tests/check_hepta_execution_install_tree.py \
  --build-dir "$BUILD_DIR" --ibapi-enabled
python3 scripts/check_hepta_agent_os_units.py
python3 scripts/check_hepta_agent_os_product_boundary.py --root .
```

`hepta-execution-runtime` 安装 PAPER binaries、repair scripts 和 PAPER units；
`hepta-agent-os-runtime` 安装 Tool Gateway runtime 和本 runbook。两者都必须安装，仅安装
Execution 组件不会更新本 runbook。`cmake --install` 是逐文件操作，不是跨文件原子事务；
因此安装前应把 staging manifest 中将被替换的现有文件保存到 root-only backup，并记录缺失
路径。在下列任一步失败时保持 `DENY_ALL`、所有 PAPER unit inactive，不得 rearm；只能从该
backup 恢复完整旧 generation 或修复后重新执行整套被动安装。

```bash
sudo systemctl disable --now hepta-local-ai-paper-agent.service || true
sudo cmake --install "$BUILD_DIR" --prefix /usr \
  --component hepta-execution-runtime
sudo cmake --install "$BUILD_DIR" --prefix /usr \
  --component hepta-agent-os-runtime
sudo systemd-tmpfiles --create \
  /usr/lib/tmpfiles.d/heptatrader-agent-os.conf
sudo systemctl daemon-reload

sed 's#  usr/#  /usr/#' "$STAGE_HASHES" | \
  sudo sha256sum --check --strict -
```

若宿主仍有待退役的 ownerless HSL5 PAPER lease，迁移只能放在上述两个组件和 hash
校验完成之后、deployment evidence 之前。此时 source policy 必须仍是 disabled
`local-only` v4，并已有匹配的 terminal end-flat receipt；broker egress 必须是
`DENY_ALL`，session/permit residue 为零，所有 PAPER runtime 以及所有 trust-domain
Tool Gateway/supervisor 实例均 inactive（cleanup interlock 是全局共享的）：

```bash
sudo /usr/libexec/hepta-prepare-paper-campaign \
  --migrate-legacy-hsl5-paper-leases
```

唯一成功标志是 `REPAIR_LEGACY_HSL5_PAPER_LEASES_RETIRED`，并且必须同时声明
`paper_authorized=false`、`live_authorized=false`、`mutation_authorized=false`。迁移把精确
终态 PAPER records 从 HSL5 store 退役并写回 HSL6；它不创建 session、permit 或任何交易
authority。原 HSL5 bytes 必须保存在 root:root、regular、single-link、mode `0400` 的
`/var/lib/hepta-local-ai-paper-agent/legacy-hsl5-paper-lease-store.backup.hsl2`，完成 receipt
必须是 root:root、regular、single-link、mode `0600` 的
`/var/lib/hepta-local-ai-paper-agent/legacy-hsl5-paper-cleanup.receipt.json`，且临时 intent 不得
残留。不得直接调用低层 `hepta-sessionctl`。任何检查或迁移失败都必须保持 `DENY_ALL`，不得
继续记录 deployment evidence 或 prepare 新 campaign。

上述逐文件安装和 hash 检查完成后，仍不得直接进入 v4 `prepare`。独立安装事务必须先把
固定的 `/etc/heptatrader/local-ai-paper-certified-install-closure-v1.json` 发布为
root:root、regular、single-link、mode `0600` 的 canonical certified install closure，
并由调用方提供该文件的外部 SHA-256；当前 policy、调用方自声明或一份自洽的旧安装都不能
替代该外部 pin。只使用安装后的实际 CLI 封存 non-authorizing deployment evidence：

```bash
CERTIFIED_INSTALL_CLOSURE_SHA256='sha256:<64-lowercase-hex>'
sudo /usr/libexec/hepta-prepare-paper-campaign \
  --record-deployment-evidence \
  --certified-install-closure \
    /etc/heptatrader/local-ai-paper-certified-install-closure-v1.json \
  --certified-install-closure-sha256 "$CERTIFIED_INSTALL_CLOSURE_SHA256"
```

该入口只接受上述固定 closure 路径，逐项复核 closure 中完整、有序的 63 个 installed
path/hash/mode（包括 IB units/drop-ins、Agent-OS transport、tmpfiles、trust-domain runtime，
以及 external-P1 的 root finalizer、same-boot authority guardian、finalizer socket/service）
runtime），并原子发布 root:root `0600` 的
`/etc/heptatrader/local-ai-paper-deployment-v1.json`。deployment evidence 必须精确声明
`paper_authorized=false`、`live_authorized=false`、`mutation_authorized=false`；它只是安装
字节与 source/install transaction 的绑定，不是 PAPER 批准，也不能单独 rearm 或下单。

该 evidence 不得进入 active v4 `prepare`。v4 source policy 必须在读取
strategy、deployment evidence、创建 WAL 或任何 systemd/config 变更前拒绝，
稳定错误码为 `REPAIR_P1_ADMISSION_REQUIRED`。在已经 end-flat、DENY_ALL且
runtime inactive 的边界上，evidence recorder 会把 disabled v4 安装 seed 原子
迁移为 disabled v5 `local-only` seed，source 以新 certified closure 为唯一依据，
同时绑定 MKT strategy 与 deployment file/body/transaction pins。campaign operator
对任何 enabled 或 mutation-authorized v4 policy 均在 provider/disarm 前拒绝，错误码为
`CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED`。checked-in disabled 零 pin example 和新生成的
deployment evidence 仍只是 non-authorizing 安装证明。

恢复也不得把旧 active-v4 WAL 当成可提交交易。无论 WAL 处于哪个持久化阶段，
即使 target policy 已发布，恢复必须先 fence authority，再在 DENY_ALL、无
session/permit 残留且 runtime inactive 的边界上 rollback 到原 disabled policy/env；只有
rollback 完成才可删除 WAL。

V5 同时保留上述 external-P1 后继协议：它 exact-pin P1 audit、WATCH-to-PAPER handoff、终态
admission/finalization graph、source、strategy、domain、campaign、时间窗口和 deployment
identity，并在 root lifecycle/authority locks 内 descriptor-bound 重开。Prepare 使用独立 WAL
v2 持久化全部绑定；任何崩溃或重启都只允许 fence+rollback。首次 cycle 必须在 candidate
仍新鲜时原子生成 root-only consumption receipt，后续 cycle 重开该 receipt 及全部 immutable
pins，而不是把过期 candidate 重新解释成授权。Caller 自声明或仅自洽的 digest 仍绝不能
代替独立终态 P1 证据。

覆盖 unit 文件不会清除旧 enable symlink 或 drop-in。部署后、任何 `rearm-stack` 之前，必须
验证 agent 是 static one-shot admission boundary，而不是可 enable/restart 的 daemon：

```bash
AGENT_UNIT=hepta-local-ai-paper-agent.service
test "$(systemctl show "$AGENT_UNIT" -p UnitFileState --value)" = static
test "$(systemctl show "$AGENT_UNIT" -p Restart --value)" = no
test -z "$(systemctl show "$AGENT_UNIT" -p DropInPaths --value)"
test "$(systemctl show "$AGENT_UNIT" -p FragmentPath --value)" = \
  /usr/lib/systemd/system/hepta-local-ai-paper-agent.service
systemctl show "$AGENT_UNIT" -p ExecCondition -p Requisite \
  -p InaccessiblePaths --no-pager

test -z "$(find /etc/systemd/system /run/systemd/system -type l \
  -name hepta-local-ai-paper-agent.service -print -quit 2>/dev/null)"
```

effective unit 必须只有精确的
`ExecCondition=/usr/libexec/hepta-local-paper-repair pre-start-guard`，并 `Requisite=`
Execution、Tool Gateway、operator socket、safe-recovery、session-renew、supervisor 和绝对
stop 共七项；`InaccessiblePaths=` 必须隔离
`/var/lib/hepta-local-ai-paper-agent/session-authority`。`DropInPaths` 非空、发现任何旧 wants
symlink、`UnitFileState` 非 `static` 或有效配置与 staging 不一致时立即停止；审查并移除旧
配置后，从 `daemon-reload` 和全部 hash/effective-unit 检查重新开始。最后再次运行上述两个
DENY_ALL 检查，确认部署没有改变授权状态。

## 本地 AI PAPER：v5 24 小时路径

V4 仅作为迁移输入，不再提供 active authority。部署完成后，
`--record-deployment-evidence` 在 terminal-flat/DENY_ALL/inactive 边界上将它迁移为
disabled v5 `local-only` seed。普通 prepare 从该 seed 动态生成 fresh campaign ID
与新的 86400 秒窗口，不复用旧 WAL/receipt/campaign ID。
不得再执行旧 active-v4 五步启动链；v5-local 必须走新 WAL 与 manual-start 边界。

安全操作仅限保持实时 DENY_ALL、PAPER units inactive，并让已有 incident/campaign 通过
既有 risk-recovery/end-flat 路径收敛到 terminal flat receipt。不要手工删除 safety marker、
session token、durable revoke bearer、checkpoint、permit 或 prepare WAL。安装事务仍可用
`--record-deployment-evidence` 封存部署字节证明，但该模式不会创建 campaign 或授予任何 mutation。

fresh v4 prepare 必须在读取 strategy、deployment evidence、创建 WAL 或改变 systemd/config
之前返回 `REPAIR_P1_ADMISSION_REQUIRED`。operator 对任何 enabled 或
mutation-authorized v4 policy 必须在 provider/disarm 前返回
`CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED`。旧 active-v4 WAL（包括 policy 已发布）
必须先 fence authority，再在 DENY_ALL、无 session/permit 残留、runtime inactive 的边界上
rollback；rollback 完成前不得删除 WAL。

V5 policy 有两个互斥 exact shape。`external-p1-finalized` 保留 LMT/DAY 与
P1/WATCH/finalization graph 全部 pins，并固定为单次、单数量 canary
（`max_cycles=1`、`max_quantity=1`、`max_active_orders=1`）与精确 300 秒窗口；它只接受 canonical
v2 handoff 路径，且必须重证 forward-only restoration 后的当前 dormant PAPER
`alpha.env` 以及 handoff 事务封存的 exact 767-byte PAPER runtime profile
（SHA-256 `99dd8ab1cd612989906a972abcaad0dd4234d908ea4ce295c0c01a9059604ee4`）。
外部入口不得把 legacy 25000/30000ms runtime profile 在本地改写成可用形状。
Handoff v1 一律拒绝。`local-only` 使用 MKT/DAY，仅限
EUR.USD、单笔 25000、唯一 active order、最长 24 小时/720 cycles、强制
end-flat，并绑定 certified deployment bytes；它不包含、不伪装 P1/WATCH fields。
两种 shape 均不放宽 LIVE，任何 LIVE 标志仍永久拒绝。

### V5-0. 从真实终态证据生成 disabled canonical policy

这一节只物化一个 `enabled=false`、`mutations_authorized=false` 的 policy。它不会生成、签署或
修补 P1 audit、WATCH-to-PAPER handoff、admission candidate、zero-exposure、finalization 或
deployment evidence。缺任何一项、digest 不一致、元数据不安全或 candidate 已过期时，脚本必须
在写 policy 前退出。不得把 example 中的零 digest 或本机计算出的“替代 receipt”填进去。

先进入 root shell，并给本次 external-P1 canary 选择一个按整秒对齐的、精确 300 秒窗口。窗口起点必须
不早于 admission candidate 的 `evaluated_at_ms`，首次 cycle 还必须发生在 candidate 与
zero-exposure receipt 各自的有效期内。`AUTH_*` 是已实际探测并获准的非秘密 profile 绑定；
不要编造 generation，也不要把 credential 写入这里。

```bash
sudo -i
set -euo pipefail
umask 077

export VALID_AFTER_MS="$(( $(date +%s) * 1000 ))"
export EXPIRES_AT_MS="$(( VALID_AFTER_MS + 300000 ))"
export AUTH_GENERATION='replace-with-probed-auth-generation'
export AUTH_PROFILE_ID='replace-with-approved-paper-profile-id'

test "$(( EXPIRES_AT_MS - VALID_AFTER_MS ))" -eq 300000
test "$(( VALID_AFTER_MS % 1000 ))" -eq 0
test "$(( EXPIRES_AT_MS % 1000 ))" -eq 0
test "$AUTH_GENERATION" != replace-with-probed-auth-generation
test "$AUTH_PROFILE_ID" != replace-with-approved-paper-profile-id
```

以下 materializer 固定读取 production finalization pointer 和 certified deployment 路径；
candidate、tombstone、P1 audit 与 handoff 的路径只从已封存 graph 中导出。它只接受 root:root
`0600`、单链接、非 symlink、compact canonical JSON，并原子写入 disabled policy：

```bash
python3 - <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

ADMISSION_ROOT = Path("/var/lib/hepta/paper-testing-admission")
HOST_ROOT = Path("/run/hepta/ib-paper-host-authority")
POINTER_PATH = HOST_ROOT / "finalization-current.v1.json"
DEPLOYMENT_PATH = Path(
    "/etc/heptatrader/local-ai-paper-deployment-v1.json")
STRATEGY_PATH = Path(
    "/usr/share/heptatrader/hepta-local-ai-paper-strategy-v3.json")
TEMPLATE_PATH = Path(
    "/usr/share/doc/heptatrader/examples/"
    "hepta-ib-paper-campaign-policy-p1-v5.json.example")
POLICY_PATH = Path("/etc/heptatrader/paper-campaigns/alpha.json")
BOUNDARY = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
TOMBSTONE = re.compile(
    r"finalized\.zero-exposure-[0-9a-f]{48}\.v1\.json")


def canonical(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")


def file_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def abort(label: str) -> None:
    raise SystemExit("V5_POLICY_MATERIALIZATION_REFUSED:" + label)


def load_sealed(path: Path, label: str) -> tuple[dict, bytes, dict]:
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        abort(label + "_PATH")
    try:
        metadata = os.lstat(path)
    except OSError:
        abort(label + "_MISSING")
    if (stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or metadata.st_uid != 0 or
            metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600):
        abort(label + "_METADATA")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        raw = b""
        while len(raw) <= 4 * 1024 * 1024:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns)
    if len(raw) > 4 * 1024 * 1024 or not raw or not (
            identity(metadata) == identity(opened) == identity(after)):
        abort(label + "_CHANGED")
    try:
        document = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        abort(label + "_JSON")
    if not isinstance(document, dict) or raw != canonical(document):
        abort(label + "_NON_CANONICAL")
    claimed = document.get("body_sha256")
    body = dict(document)
    body.pop("body_sha256", None)
    if (not isinstance(claimed, str) or claimed == "sha256:" + "0" * 64 or
            claimed != file_digest(canonical(body))):
        abort(label + "_BODY_DIGEST")
    return document, raw, {
        "path": str(path), "file_sha256": file_digest(raw),
        "body_sha256": claimed}


def load_installed(path: Path, label: str) -> bytes:
    metadata = os.lstat(path)
    if (stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or metadata.st_uid != 0 or
            metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o644):
        abort(label + "_METADATA")
    raw = path.read_bytes()
    if os.lstat(path) != metadata:
        abort(label + "_CHANGED")
    return raw


def load_existing_policy(path: Path) -> dict:
    metadata = os.lstat(path)
    if (stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or metadata.st_uid != 0 or
            metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600):
        abort("OLD_POLICY_METADATA")
    raw = path.read_bytes()
    if os.lstat(path) != metadata:
        abort("OLD_POLICY_CHANGED")
    try:
        document = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        abort("OLD_POLICY_JSON")
    if not isinstance(document, dict) or raw != canonical(document):
        abort("OLD_POLICY_NON_CANONICAL")
    return document


def require_ref(value: object, observed: dict, label: str) -> None:
    if value != observed:
        abort(label + "_REFERENCE")


valid_after = int(os.environ["VALID_AFTER_MS"])
expires_at = int(os.environ["EXPIRES_AT_MS"])
if (valid_after % 1000 or expires_at % 1000 or
        expires_at - valid_after != 300_000):
    abort("WINDOW")

pointer, _pointer_raw, pointer_ref = load_sealed(POINTER_PATH, "POINTER")
if (pointer.get("schema") !=
        "hepta.p1-paper-zero-exposure-finalization-current.v1" or
        pointer.get("version") != 1 or pointer.get("status") != "CURRENT"):
    abort("POINTER_SEMANTIC")
tombstone_value = pointer.get("finalization_tombstone_reference")
if not isinstance(tombstone_value, dict):
    abort("TOMBSTONE_REFERENCE")
tombstone_path = Path(str(tombstone_value.get("path", "")))
if tombstone_path.parent != HOST_ROOT or not TOMBSTONE.fullmatch(
        tombstone_path.name):
    abort("TOMBSTONE_PATH")
tombstone, _tombstone_raw, tombstone_ref = load_sealed(
    tombstone_path, "TOMBSTONE")
require_ref(tombstone_value, tombstone_ref, "TOMBSTONE")
if (tombstone.get("schema") !=
        "hepta.p1-paper-zero-exposure-reservation-finalization.v1" or
        tombstone.get("version") != 1 or
        tombstone.get("status") != "ADMISSION_GO"):
    abort("TOMBSTONE_SEMANTIC")

candidate_value = tombstone.get("candidate_reference")
if not isinstance(candidate_value, dict):
    abort("CANDIDATE_REFERENCE")
candidate_path = Path(str(candidate_value.get("path", "")))
if (candidate_path.parent != ADMISSION_ROOT or
        not candidate_path.name.endswith(".json") or "/" in candidate_path.name):
    abort("CANDIDATE_PATH")
candidate, _candidate_raw, candidate_ref = load_sealed(
    candidate_path, "CANDIDATE")
require_ref(candidate_value, candidate_ref, "CANDIDATE")
if (candidate.get("schema") !=
        "hepta.paper-testing-admission-candidate-receipt.v1" or
        candidate.get("version") != 1 or candidate.get("status") != "GO" or
        candidate.get("paper_test_admission_candidate") is not True or
        candidate.get("authorization_effect") !=
        "NONE_READ_ONLY_CANDIDATE_ONLY"):
    abort("CANDIDATE_SEMANTIC")
if any(candidate.get(field) is not False for field in BOUNDARY):
    abort("CANDIDATE_AUTHORITY")
if any(document.get(field) is not False
       for document in (pointer, tombstone)
       for field in BOUNDARY):
    abort("FINALIZATION_AUTHORITY")

campaign_id = candidate.get("campaign_id")
domain_id = candidate.get("domain")
source_sha = candidate.get("source_baseline_sha256")
strategy_sha = candidate.get("strategy_sha256")
if (not isinstance(campaign_id, str) or not IDENTIFIER.fullmatch(campaign_id) or
        domain_id != "alpha" or
        any(document.get("campaign_id") != campaign_id
            for document in (pointer, tombstone)) or
        any(document.get("domain") != domain_id
            for document in (pointer, tombstone)) or
        any(document.get("source_baseline_sha256") != source_sha
            for document in (pointer, tombstone))):
    abort("FINALIZATION_BINDING")
now_ms = time.time_ns() // 1_000_000
if (not isinstance(candidate.get("evaluated_at_ms"), int) or
        not isinstance(candidate.get("expires_at_ms"), int) or
        valid_after < candidate["evaluated_at_ms"] or
        not now_ms < candidate["expires_at_ms"] or
        valid_after >= candidate["expires_at_ms"]):
    abort("CANDIDATE_WINDOW")

bindings = candidate.get("input_bindings")
if not isinstance(bindings, dict) or len(bindings) != 17:
    abort("CANDIDATE_INPUT_BINDINGS")
pinned = {}
for name in ("p1_audit_receipt", "watch_handoff_receipt"):
    binding = bindings.get(name)
    if not isinstance(binding, dict):
        abort(name.upper() + "_BINDING")
    document, _raw, observed = load_sealed(
        Path(str(binding.get("path", ""))), name.upper())
    require_ref(
        {key: binding.get(key) for key in ("path", "file_sha256", "body_sha256")},
        observed, name.upper())
    if any(binding.get(key) != document.get(key)
           for key in ("schema", "version", "status")):
        abort(name.upper() + "_SEMANTIC")
    pinned[name] = observed

deployment, _deployment_raw, deployment_ref = load_sealed(
    DEPLOYMENT_PATH, "DEPLOYMENT")
if (deployment.get("schema") !=
        "hepta.local-ai-paper-deployment-evidence.v1" or
        deployment.get("version") != 1 or
        deployment.get("source_baseline_sha256") != source_sha or
        any(deployment.get(field) is not False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized")) or
        not isinstance(deployment.get("install_transaction_id"), str)):
    abort("DEPLOYMENT_SEMANTIC")
strategy_raw = load_installed(STRATEGY_PATH, "STRATEGY")
if file_digest(strategy_raw) != strategy_sha:
    abort("STRATEGY_DIGEST")

template_raw = load_installed(TEMPLATE_PATH, "TEMPLATE")
try:
    policy = json.loads(template_raw.decode("ascii"))
except (UnicodeError, json.JSONDecodeError):
    abort("TEMPLATE_JSON")
policy.update({
    "campaign_id": campaign_id,
    "domain_id": domain_id,
    "enabled": False,
    "mutations_authorized": False,
    "valid_after_ms": valid_after,
    "expires_at_ms": expires_at,
    "max_cycles": 1,
    "source_baseline_sha256": source_sha,
    "strategy_sha256": strategy_sha,
    "admission_receipt_name": candidate_path.name,
    "admission_receipt_file_sha256": candidate_ref["file_sha256"],
    "admission_receipt_body_sha256": candidate_ref["body_sha256"],
    "admission_finalization_current_pointer_path": str(POINTER_PATH),
    "admission_finalization_current_pointer_file_sha256":
        pointer_ref["file_sha256"],
    "admission_finalization_current_pointer_body_sha256":
        pointer_ref["body_sha256"],
    "admission_finalization_tombstone_path": str(tombstone_path),
    "admission_finalization_tombstone_file_sha256":
        tombstone_ref["file_sha256"],
    "admission_finalization_tombstone_body_sha256":
        tombstone_ref["body_sha256"],
    "deployment_evidence_file_sha256": deployment_ref["file_sha256"],
    "deployment_evidence_body_sha256": deployment_ref["body_sha256"],
    "deployment_install_transaction_id":
        deployment["install_transaction_id"],
    "p1_audit_receipt_path": pinned["p1_audit_receipt"]["path"],
    "p1_audit_receipt_file_sha256":
        pinned["p1_audit_receipt"]["file_sha256"],
    "p1_audit_receipt_body_sha256":
        pinned["p1_audit_receipt"]["body_sha256"],
    "watch_handoff_receipt_path":
        pinned["watch_handoff_receipt"]["path"],
    "watch_handoff_receipt_file_sha256":
        pinned["watch_handoff_receipt"]["file_sha256"],
    "watch_handoff_receipt_body_sha256":
        pinned["watch_handoff_receipt"]["body_sha256"],
})
payload = canonical(policy)
POLICY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chown(POLICY_PATH.parent, 0, 0)
os.chmod(POLICY_PATH.parent, 0o700)
if POLICY_PATH.exists() or POLICY_PATH.is_symlink():
    old = load_existing_policy(POLICY_PATH)
    if (old.get("enabled") is not False or
            old.get("mutations_authorized") is not False):
        abort("OLD_POLICY_ACTIVE")
descriptor, temporary = tempfile.mkstemp(
    prefix=".alpha.v5.", dir=str(POLICY_PATH.parent))
try:
    os.fchmod(descriptor, 0o600)
    os.fchown(descriptor, 0, 0)
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])
    os.fsync(descriptor)
    os.close(descriptor)
    descriptor = -1
    os.replace(temporary, POLICY_PATH)
    directory_fd = os.open(POLICY_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    if os.path.exists(temporary):
        os.unlink(temporary)
print(
    "V5_DISABLED_POLICY_READY campaign_id=" + campaign_id +
    " policy_sha256=" + file_digest(payload))
PY
```

脚本成功后仍只得到 disabled policy。立即确认 canonical bytes 与四个授权边界；任何检查失败都
保持 DENY_ALL，不得继续：

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/etc/heptatrader/paper-campaigns/alpha.json')
raw = p.read_bytes()
d = json.loads(raw)
canonical = (json.dumps(d, ensure_ascii=True, sort_keys=True,
                        separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')
assert raw == canonical
assert d['schema'] == 'hepta.ib-paper-campaign-policy.v5'
assert d['admission_mode'] == 'external-p1-finalized'
assert d['enabled'] is False and d['mutations_authorized'] is False
assert d['paper_only'] is True and d['live_authorized'] is False
assert d['expires_at_ms'] - d['valid_after_ms'] == 300000
assert d['max_cycles'] == 1
assert d['max_quantity'] == 1
assert d['watch_handoff_receipt_path'] == \
    '/var/lib/hepta/p1-admission/p1-watch-to-paper-handoff-receipt-v2.json'
print('V5_DISABLED_POLICY_VERIFIED campaign_id=' + d['campaign_id'])
PY
```

### V5-1. Prepare、rearm、auth、acceptance 与人工 start

Prepare 必须 exact-match policy 中的窗口与单次 cycle；它不会从命令行重写已封存
窗口。成功标志必须包含 `REPAIR_CAMPAIGN_READY`、`manual_start_required=true`、
`order_type=LMT`、`tif=DAY`：

```bash
/usr/libexec/hepta-prepare-paper-campaign \
  --duration-seconds 300 \
  --max-cycles 1 \
  --auth-generation "$AUTH_GENERATION" \
  --auth-profile-id "$AUTH_PROFILE_ID"

CAMPAIGN_ID="$(python3 -c \
  "import json; print(json.load(open('/etc/heptatrader/paper-campaigns/alpha.json'))['campaign_id'])")"
test -n "$CAMPAIGN_ID"
```

外部 P1 路径不得复用下面的 local-only `strategy-acceptance`；该命令会发送 25000 数量的
MKT 往返，与 quantity-one LMT canary 合约不相容。Round114 安装树提供唯一允许的
transactional external flow：被动安装的 capture/executor/root-coordinator units 默认不启用，
调用者只通过 root coordinator 的 request-scoped 入口启动一次 canary。先确认完整 wrapper 与
unit 闭包已安装，再给首次 cycle 选择一个持久保存、长度不超过 128 的 canonical ID；崩溃恢复
或同一 cycle 重试必须复用该 ID，不得生成新 ID：

```bash
test -x /usr/libexec/hepta-p1-paper-canary-root-coordinator
test -x /usr/libexec/hepta-p1-paper-canary-launch-joiner
test -x /usr/libexec/hepta-p1-paper-canary-executor
test -x /usr/libexec/hepta-p1-paper-canary-terminal-prover
test -f /usr/lib/systemd/system/hepta-p1-paper-canary-capture.service
test -f /usr/lib/systemd/system/hepta-p1-paper-canary-executor.service
test -f /usr/lib/systemd/system/hepta-p1-paper-canary-root-coordinator.service
test -f /usr/lib/systemd/system/hepta-p1-paper-canary-finalizer.socket
test -f /usr/lib/systemd/system/hepta-p1-paper-canary-finalizer@.service

export CYCLE_ID='replace-with-persisted-external-p1-cycle-id'
test "$CYCLE_ID" != replace-with-persisted-external-p1-cycle-id
sudo /usr/libexec/hepta-p1-paper-canary-root-coordinator \
  --campaign-id "$CAMPAIGN_ID" \
  --cycle-id "$CYCLE_ID"
```

成功输出是 canonical JSON，`authority_granted` 必须为 `false`，`status` 只能是
`P2_SUCCESS` 或 `NO_TRADE`；`RECOVERY_REQUIRED` 或非零退出一律保持 fail-closed。外部终态
receipt 固定在
`/var/lib/hepta/p1-paper-canary-control/$CAMPAIGN_ID/$CYCLE_ID/cycle-completion-receipt.v3.json`。
`P2_SUCCESS` 只接受 normal v3 root cleanup：240 秒 cleanup 窗口、八个固定动作，且
`root-cleanup-receipt.v3.json` 必须绑定
`durable-owner-retirement-receipt.v3.json` 中的 `HSL8_ATOMIC_TERMINAL_ACK`、terminal ACK
和 fresh current-runtime exact replay。这里的 `credentials_destroyed=true` 只表示 peer
mutation token 与 authority document 已销毁；receipt 必须同时明确记录唯一 retained root
recovery bearer 的 path/hash/count、`mutation_authority=false`。该 bearer 在 outer
`cycle-completion-receipt.v3.json` durable 之前不得删除；outer completion durable 后只能由
terminal prover 的 completion-bound purge intent/receipt 删除，丢失响应时复用同一 campaign/cycle、
同一 terminal ACK 与相同 receipt bytes，不能把 bare absence 当作成功。

coordinator v3 WAL 是 forward-recovery 状态机。重启必须按已持久化 phase reopen 并校验原
capture、normalization/no-trade、handoff、execution result、inner request/receipt、completion 与
purge receipt；不得再次运行 launch joiner、不得新建 capture/PAPER owner、不得更换 cycle ID。
completion publish 或 purge 响应丢失时只允许 byte-identical replay。旧 normal v1/v2 receipt、
旧 v1/v2 coordinator WAL 或旧 v1/v2 outer
completion 只会 fail-closed，不能作为成功或恢复捷径。emergency 保持 v1、45 秒和五个 fail-close 动作，
其终态只能是 `RECOVERY_REQUIRED`/`RECOVERY_ONLY`，永远不能回填为 `P2_SUCCESS`。
executor 与 root coordinator 的 systemd 上限分别为 10 分钟和 15 分钟；公开 coordinator
入口最多等待 16 分钟，为 ExecStopPost 和终态证明保留边界。
不能跳步、并行、复用别的 cycle receipt，不能直接启动 agent unit，也不能手工启动上述
systemd units。local-only `strategy-acceptance` 是 order-mutating 阶段；在运行它之前必须人工
确认账号确为 PAPER、preflight 为 `position=0 active_orders=0 gross=0`，并接受这笔测试订单。

#### LOCAL-ONLY ONLY：24 小时 MKT campaign（禁止用于 external-P1）

下面整个命令块只适用于 canonical `admission_mode=local-only` seed。先独立 prepare 本地
24 小时/720-cycle policy；若 policy 是 `external-p1-finalized`，任何一条都不得执行：

```bash
/usr/libexec/hepta-prepare-paper-campaign \
  --duration-seconds 86400 \
  --max-cycles 720 \
  --auth-generation "$AUTH_GENERATION" \
  --auth-profile-id "$AUTH_PROFILE_ID"

/usr/libexec/hepta-local-paper-repair rearm-stack
# 必须：REARM_STACK_READY ... position=0 active_orders=0 gross=0

/usr/libexec/hepta-local-paper-repair auth-rearm \
  --profile-id "$AUTH_PROFILE_ID" \
  --auth-generation "$AUTH_GENERATION"
# 必须：AUTH_REARM_COMPLETE position=0 active_orders=0 gross=0

/usr/libexec/hepta-local-paper-repair strategy-acceptance \
  --initial-wait-seconds 60
# 必须同时出现：
# STRATEGY_ACCEPTANCE_ENTRY_CONFIRMED side=SELL quantity=25000
# STRATEGY_ACCEPTANCE_EXIT_CONFIRMED trigger=MODEL_REVERSAL ... position=0 ... gross=0

/usr/libexec/hepta-local-paper-repair start-campaign
# 必须：CAMPAIGN_START_COMPLETE agent=active 以及五个 safety timer=active
```

`start-campaign` 前的有效依赖必须是 active：
`hepta-tool-gateway@alpha.service`、`hepta-execution-ib-paper@alpha.service` 与
`hepta-ib-paper-campaign-operator@alpha.socket`。若命令报告 start rollback，必须保留其自动
启用的 safe-recovery timer；不得随后手工 `systemctl start` agent。

### V5-2. LOCAL-ONLY 首次 consumption、循环监控与 24 小时 deadline

首次成功 `open_cycle` 会原子创建 root-only consumption receipt；在此之前文件不存在是正常
状态，绝不能手工创建。路径只由 domain 与 campaign ID 计算：

```bash
CONSUMPTION_PATH="$(python3 - "$CAMPAIGN_ID" <<'PY'
import hashlib, sys
campaign = sys.argv[1].encode('ascii')
name = hashlib.sha256(b'alpha\0' + campaign).hexdigest()
print('/var/lib/hepta/ib-paper-campaign/consumption.' + name + '.v1.json')
PY
)"

until test -f "$CONSUMPTION_PATH"; do
  sudo -u hepta-agent-alpha /usr/bin/hepta-campaignctl \
    --domain alpha --campaign-id "$CAMPAIGN_ID" \
    --request-id "status-$(date +%s%N)" status
  sleep 5
done

stat -c '%U:%G %a %h %s %n' "$CONSUMPTION_PATH"
python3 - "$CONSUMPTION_PATH" "$CAMPAIGN_ID" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
raw = p.read_bytes()
d = json.loads(raw)
body = dict(d); claimed = body.pop('body_sha256')
canonical = lambda value: (json.dumps(
    value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
    allow_nan=False) + '\n').encode('ascii')
assert raw == canonical(d)
assert claimed == 'sha256:' + hashlib.sha256(canonical(body)).hexdigest()
assert d['schema'] == 'hepta.ib-paper-campaign-consumption-state.v1'
assert d['status'] == 'CONSUMED' and d['domain_id'] == 'alpha'
assert d['campaign_id'] == sys.argv[2]
assert d['authorization_effect'] == 'NONE_STATE_ONLY'
for field in ('paper_authorized', 'live_authorized', 'mutation_authorized',
              'direct_broker_access', 'order_submission_authorized'):
    assert d[field] is False
print('V5_FIRST_CONSUMPTION_VERIFIED consumed_at_ms=' + str(d['consumed_at_ms']))
PY
```

记录第一次验证时的 inode、file SHA-256 与 body SHA-256。后续 cycle 必须重开同一 receipt 并
保持所有 immutable pins；inode/content 变化、receipt 消失、跨 boot、monotonic expiry、P1/
handoff/finalization/deployment 任一重开失败都应让 operator 拒绝并触发恢复，不能生成新 receipt
“修复”。运行期间至少监控以下状态：

```bash
export CAMPAIGN_ID
watch -n 30 -- sh -c '
  exec sudo -u hepta-agent-alpha /usr/bin/hepta-campaignctl \
    --domain alpha --campaign-id "$CAMPAIGN_ID" \
    --request-id "status-$(date +%s%N)" status
'

systemctl --no-pager --full status \
  hepta-local-ai-paper-agent.service \
  hepta-local-ai-paper-24h-stop.timer \
  hepta-local-ai-paper-end-flat-retry.timer \
  hepta-local-paper-safe-recover.timer \
  hepta-local-paper-session-renew.timer \
  hepta-local-paper-supervisor.timer
```

deadline timer 到期会请求并重试 end-flat。提前结束或 deadline 后人工确认时，使用同一条 durable
路径；不要先 stop agent/timer：

```bash
/usr/libexec/hepta-local-paper-repair request-end-flat
# 必须：END_FLAT_REQUESTED ...

/usr/libexec/hepta-local-paper-repair end-flat-condition && \
  /usr/libexec/hepta-local-paper-repair end-flat
# 必须：END_FLAT_COMPLETE ... position=0 active_orders=0 gross=0
```

只有看到 `END_FLAT_COMPLETE` 且 terminal receipt 已封存，才能把 24 小时任务记为完成。若
`end-flat-condition` 暂未满足或 `end-flat` 失败，durable request 与 retry timer 必须保留；
不得删除 request、session、permit、safety marker、checkpoint、consumption receipt 或 WAL。

### V5-3. Fail-closed 处置

- materialization/prepare 任一步失败：不运行后续命令；保持 DENY_ALL。Prepare WAL v2 必须由
  下一次官方 prepare/recovery 在 lifecycle lock 下 reconcile，不能手工删除。
- `rearm-stack` 或 `auth-rearm` 失败：保留自动 recovery 状态，不使用同一 campaign ID 重试，
  不删 receipt 后重新生成。
- `strategy-acceptance` 入场、反转退出或零暴露确认失败：立即让 safe-recovery/end-flat 收敛；
  该 campaign 视为 terminal，不 replay acceptance。
- `start-campaign` 失败：依赖官方 rollback（stop agent、恢复 timer、必要时 enable safe recovery）；
  不以 direct `systemctl start` 绕过。
- 首次 consumption 缺失、后续 receipt/pin 漂移、status/supervisor/timer 异常：停止增加风险，执行
  `request-end-flat`/`end-flat`；禁止合成 receipt 或手工清理 authority 状态。

## 1. 启动前检查（5 分钟）

1. 拉取最新代码并确认分支正确。
2. 执行 CI 门禁（建议本地）：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\gate-local.ps1 -NoLaunch -SkipHealthcheck
   ```
3. 检查配置来源与 profile（paper/live/sim）一致。
4. 确认 IB Gateway/TWS 账号为目标环境（Paper 优先）。

## 2. 启动流程

1. 启动 IB Gateway/TWS。
2. 运行离线 Tool Gateway/Simulator 回归（不会连接 broker）：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_regression_round.ps1 -ProjectRoot "D:\quant\HeptaTrader-master" -BuildDir ".\build-agent-os-ci"
   ```
3. 启动主程序并落盘日志：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run_hepta_with_logs.ps1
   ```
4. 对最新日志执行汇总：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\summarize_ib_logs.ps1
   ```

## 3. 启动成功判定

- `nextValidId` 存在。
- 有持续 `tickPrice`（或符合策略无行情预期）。
- 无 P1 告警（见 `alerts.json`）。
- `ci_gate` 最近一次为 PASS。

## 4. 启动失败回退

1. 立即停止策略下单路径。
2. 保留并打包 `runtime-logs` 最新目录。
3. 按 `RUNBOOK-INCIDENT.md` 分诊。

## 5. Round35 仓库治理

统一 ops 入口：

```bash
ROUND35_ARTIFACT_DIR=heptatrader-round35-semantic-delivery-artifacts-v6
ROUND35_ARTIFACT_ROOT=runtime-logs/$ROUND35_ARTIFACT_DIR
python3 scripts/hepta_ops.py status
python3 scripts/hepta_ops.py report \
  --round 35 \
  --release-version 0.1.0-beta.1-round35 \
  --source-baseline "$ROUND35_ARTIFACT_ROOT/source-baseline-manifest.json" \
  --source-baseline-artifact-root "$ROUND35_ARTIFACT_ROOT" \
  --output runtime-logs/heptatrader-round35-hepta-ops-inventory-v2.json
python3 scripts/hepta_ops.py install \
  --output compat/hepta-ops-generated --check
```

`hepta-ops` 只执行声明式 registry 中的 canonical/compat job，使用 argv
而不是 shell 字符串，并在 Linux 上以 seccomp 拒绝 network syscalls；registry
不能赋予 network、PAPER 或 LIVE 权限。generated shim 固定使用
`/bin/sh` 与 `/usr/bin/python3`，并在调用任何外部程序前只用 shell 内建定位
repository；运行时清理 shell/Python 注入变量及 `PATH`。
如需兼容入口 telemetry，`HEPTA_OPS_TELEMETRY` 必须指向 caller-owned、mode
`0700` 私有目录内固定名 `compat-wrapper-usage.jsonl`；父级 symlink、路径替换、
非 `0600` 文件均 fail-closed。现有根 wrapper 与研究脚本仅做 inventory 分类，
本轮不删除、不覆盖。

部署引用收敛前，先在不改变 unit、cron 或进程状态的情况下生成 host inventory：

```bash
umask 077
python3 scripts/inventory_heptatrader_legacy_wrappers.py \
  --root . \
  --registry ops/hepta-ops-v1.json \
  --include-host-runtime \
  --output "$HEPTA_PRIVATE_EVIDENCE/legacy-wrapper-inventory.json"
```

输出的 outer report 为
`hepta.legacy-wrapper-retirement-inventory.v2` version 2，内含
`hepta.host-script-reference-inventory.v1`。检查
`host_runtime.complete`、`errors`、`summary`、`unique_wrapper_paths`、
`unique_direct_script_paths` 和 `script_references`；其中 systemd 记录包含
enabled/active 状态、模板及实例，cron/process 记录仅包含来源定位与匹配到的
repository script，不包含完整命令、参数、环境值或 cron 原文。报告只为后续
research worktree 与 declarative mapping 提供输入。相对命令按 systemd
`WorkingDirectory` 或 process cwd 绑定；OOS/research 路径归类为
`external-worktree`，不能误算成 product checkout。该报告不授权 restart、
删除、PAPER 或 LIVE。

`ROUND35_ARTIFACT_ROOT` 必须与生成待配对 delivery closure 时使用的
`--artifact-root` 完全相同；inventory 将 physical baseline 逐级 `O_NOFOLLOW`
稳定读取，但写入的逻辑 `path` 是该 physical file 相对 artifact root 的路径。
因此上例固定得到 `source-baseline-manifest.json`，且 SHA-256、size、mode 与 closure
的 `source-baseline-manifest` role 必须逐字段相等，不能用另一份 logical-path
参数伪造 lineage。每次重建 artifact root 时应同步更新该变量。

证据索引必须先按 retention policy 分类，再生成 content-addressed object key：

```bash
python3 scripts/hepta_ops.py run evidence.index.build -- \
  --output evidence-indexes/heptatrader-round35-certification-v2.json \
  --path heptatrader-round35-hepta-ops-inventory-v2.json \
  --path heptatrader-round35-semantic-v6-delivery-closure-v1.json \
  --path "$ROUND35_ARTIFACT_DIR/no-git-soak-ibapi-off.json" \
  --path "$ROUND35_ARTIFACT_DIR/no-git-soak-ibapi-on.json" \
  --path "$ROUND35_ARTIFACT_DIR/source-baseline-manifest.json" \
  --path "$ROUND35_ARTIFACT_DIR/strict-source-bundle.tar" \
  --path "$ROUND35_ARTIFACT_DIR/strict-source-bundle-manifest.json" \
  --path "$ROUND35_ARTIFACT_DIR/worktree-soak-ibapi-off.json" \
  --path "$ROUND35_ARTIFACT_DIR/worktree-soak-ibapi-on.json"
python3 scripts/hepta_ops.py run evidence.index.verify -- \
  --index evidence-indexes/heptatrader-round35-certification-v2.json
python3 scripts/hepta_ops.py run evidence.set.build -- \
  --index evidence-indexes/heptatrader-round35-certification-v2.json \
  --round 35 \
  --release-version 0.1.0-beta.1-round35
python3 scripts/hepta_ops.py run evidence.set.verify -- \
  --manifest \
    runtime-logs/heptatrader-round35-evidence-set-manifest-v2.json \
  --index evidence-indexes/heptatrader-round35-certification-v2.json
```

索引只能原子写入独立 `evidence-indexes/`，不能覆盖 `runtime-logs` payload；
payload 以流式 SHA-256 复验。`evidence.set.build` 只接受 trusted profile 精确要求
的 inventory-v2、delivery-closure 与 closure 精确声明的七个 delivery artifacts
共九个角色，按 index 原始字节和 records/object closure 绑定后以 `0600` 原子发布。
set verifier 从七个已绑定路径推导唯一 artifact root，逐项对齐 closure 的
SHA-256/size/mode，并调用完整 delivery-closure verifier 复验 strict source bundle、
四份 8-round soak 与 source baseline 语义后才返回。Round35 可从该
manifest-defined set 生成本地 `pending-external` ingestion request：

```bash
python3 scripts/hepta_ops.py run evidence.ingestion.request.build -- \
  --index evidence-indexes/heptatrader-round35-certification-v2.json \
  --evidence-set-manifest \
    runtime-logs/heptatrader-round35-evidence-set-manifest-v2.json
```

request 以自身 SHA-256 命名，精确绑定 index/policy/record/object closure，并始终声明
`source_files_deleted=false`、`source_removal_authorized=false`、
`paper_authorized=false`、`live_authorized=false`。仓库没有 uploader、对象存储凭据、
私钥或本地 production receipt signer。

外部 ingestion service 只有在完整上传、远端全对象 readback SHA-256、immutable
version ID、compliance object lock 或 legal hold 都成立后，才能用受信 Ed25519 key
签发 receipt。把外部公钥及其 SPKI digest 加入独立复审的 trust policy 后，离线复验：

```bash
python3 scripts/hepta_ops.py run evidence.ingestion.receipt.verify -- \
  --receipt /secure/inbox/heptatrader.receipt.json \
  --request evidence-requests/sha256-REQUEST.request.json \
  --index evidence-indexes/heptatrader-round35-certification-v2.json \
  --evidence-set-manifest \
    runtime-logs/heptatrader-round35-evidence-set-manifest-v2.json
```

仓库内 production trust policy 当前精确为 `pending-external` 且 `keys=[]`，所以当前
无法接受任何 production receipt。GitHub artifact 上传也不是 production object-store
receipt 或 retention anchor。将来切换到 `configured-external` 时，canonical verifier
只读取固定的
`/etc/heptatrader/heptatrader-evidence-receipt-trust-v1.json`，并要求该 trust
policy、公钥及完整父路径均为 root-owned 且不可 group/world 写；仓库内文件只是系统
policy 缺席时的 `pending-external` 模板。有限期对象锁必须至少延续到
`max(ingested_at, verified_at, signed_at) + retention_days`。
metadata-only 验证与 payload removal 均继续禁用；将来即使 receipt 通过，也必须另立有
审批、legal hold 和精确对象闭包的删除流程，本轮不删除源证据。

CTP vendor 边界校验：

```bash
# 干净源码 checkout / 公开 CI：只验证可分发转发层与 metadata，overlay 必须缺席。
python3 scripts/converge_ctp_vendor_headers.py --check-forwarders-only
python3 scripts/verify_heptatrader_vendor_assets.py --payload-mode absent

# 仅限已独立配置并有权使用本地 overlay 的私有验证环境。
python3 scripts/converge_ctp_vendor_headers.py --check
python3 scripts/verify_heptatrader_vendor_assets.py --payload-mode present
```

CTP 实证版本为 6.7.7，仍为 disabled experimental。缺少已复审 origin URL 与
可再分发 license，因此 `distribution_authorized=false`；proprietary header/
DLL/lib/so overlay 不得进入 Agent OS clean-source、runtime 或 PAPER
certification。clean-source 仅保留 vendor manifest/README；显式 legacy monolith
构建必须另行提供并复验本地 overlay。
默认 configure 必须保持 `-DHEPTA_BUILD_LEGACY_MONOLITH=OFF`。
