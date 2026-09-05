# Startup and Readiness

Status: current normative
Applies to: installed Simulator and separately qualified IB PAPER services
Verification: install-tree, version, config, identity, journal, snapshot and reconciliation gates
Authority: startup sequence

`process alive`、socket 可连、Broker TCP connect、一次成功 RPC 或 systemd `active` 都不等于 ready。Ready 只在完整权威状态建立后发布；任何关键阶段失败都保持 new-risk gate closed。

## Preconditions

启动前记录本次候选身份：

```bash
heptactl --version
sha256sum /usr/local/libexec/heptatrader/hepta-executiond \
  /usr/local/libexec/heptatrader/hepta-tool-gatewayd
cat /usr/local/share/doc/HeptaTrader/VERSION
python3 /usr/local/share/heptatrader/research/run_protocol.py \
  verify --manifest /usr/local/share/heptatrader/research/manifest-v1.json
```

安装 prefix 可能由发行包选择 `/usr`；运维系统必须使用 install manifest 中的实际路径，而不是混用 `/usr` 与 `/usr/local`。Binary version、installed VERSION、approved artifact manifest 和配置/qualification 记录必须一致。

配置先通过 canonical resolver：

```bash
python3 scripts/resolve_hepta_config.py \
  --config /etc/heptatrader/HeptaTraderConfig.xml \
  --profile sim --format json
```

保存 `source_sha256` 与 `canonical_sha256`。对于 PAPER，把 `--profile sim` 改为 `paper`，但只有在受保护 qualification receipt 已绑定同一 binary/config/SDK/harness/host/session 时才允许继续。

## Phase 1 — OS identities, paths and sockets

确认服务用户与关键路径：

```bash
getent passwd hepta-exec
getent passwd hepta-gateway
sudo systemd-sysusers
sudo systemd-tmpfiles --create
sudo systemctl cat hepta-execution-simulator.service
sudo systemctl cat hepta-tool-gateway.service
sudo systemctl show \
  hepta-execution-simulator.socket \
  hepta-execution-events-simulator.socket \
  hepta-tool-gateway.socket \
  hepta-tool-session-supervisor.socket \
  -p LoadState -p UnitFileState -p ActiveState -p SubState
```

拒绝条件：缺失 user/group、unexpected service definition、socket path/owner/mode 与安装 manifest 不符、状态目录可被其他 trust domain 写入、credential path 缺失/权限过宽、Simulator 与 PAPER unit 同时启用。

## Phase 2 — Simulator execution authority

Simulator 的 canonical socket/service 顺序：

```bash
sudo systemctl start \
  hepta-execution-simulator.socket \
  hepta-execution-events-simulator.socket
sudo systemctl start hepta-execution-simulator.service
sudo systemctl show hepta-execution-simulator.service \
  -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p Result
sudo journalctl -u hepta-execution-simulator.service \
  --since '-5 minutes' --no-pager
```

`hepta-execution-simulator.service` 必须以 `hepta-exec` 运行、使用 `PrivateNetwork=yes`、只允许 AF_UNIX，并与所有 IB PAPER socket/service 冲突。启动时需验证：

1. install/binary identity；
2. config/profile=`sim`；
3. execution fence credential；
4. journal path ownership与 replay；
5. execution epoch/fence 建立；
6. simulator venue 建立；
7. authoritative initial order/position state；
8. reconciliation complete；
9. risk policy与 readiness gate。

任一步不确定，服务可以保持运行用于诊断，但不能发布 risk-ready。

## Phase 3 — Gateway and session boundary

Execution 处于已验证状态后再启动 Gateway sockets/service：

```bash
sudo systemctl start \
  hepta-tool-gateway.socket \
  hepta-tool-session-supervisor.socket
sudo systemctl start hepta-tool-gateway.service
sudo systemctl show hepta-tool-gateway.service \
  -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p Result
sudo journalctl -u hepta-tool-gateway.service \
  --since '-5 minutes' --no-pager
```

Gateway 必须以 `hepta-gateway` 运行、`PrivateNetwork=yes`、无 ambient/capability bounding rights，并只通过 typed AF_UNIX interfaces 到下游。Session token/lease key 不得出现在命令行或日志。

建立受控 session 后运行只读探针：

```bash
export HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock
export HEPTA_TOOL_SESSION_TOKEN='<injected-by-controlled-session-path>'
heptactl tools list
heptactl call system.get_health
heptactl watch snapshot EUR.USD
```

不要把真实 token 写进 shell history、runbook evidence 或 CI。`tools list` 只证明 discovery；`system.get_health` 和 watch snapshot 还必须携带 current epoch/generation/freshness，且与 journal/reconcile事实一致。

## Phase 4 — Readiness decision

只有以下全部为真时，operator 才能把 Simulator 标为 ready：

- exact binary/version/config digests 已记录；
- services、UID/GID、socket/path modes 与 unit contract 一致；
- journal open、writable、not poisoned，replay 无 fatal error；
- current execution epoch/fence 已建立，旧 session/permit 已失效；
- authoritative snapshot 完整、未 stale、generation current；
- open orders/positions 与 simulator venue reconcile；
- risk policy revision、fixed numeric policy 和 required quote freshness通过；
- kill switch/terminal latch 状态允许当前环境；
- required telemetry 不超 series cap，P1 counter 未发生新增；
- safe-exit lane 可用。

Ready evidence 至少保存 version、binary/config digests、unit identity、journal health、epoch/fence、snapshot generation、reconcile result、risk policy revision 和 operator identity；不得保存 credential/token/raw account ID。

## PAPER startup boundary

PAPER 使用 `hepta-execution-ib-paper.socket`、`hepta-execution-events-ib-paper.socket` 和 `hepta-execution-ib-paper.service`，并要求 broker egress policy 与 kill-switch control directory。未经 `G-IB-001` exact-artifact protected qualification 时，禁止启动 mutation campaign。Repository build、mock、Simulator、TCP connect 或旧 qualification receipt 都不能替代当前 binary/config/session 的资格。

PAPER 启动额外检查：official IB API identity、TWS/IB Gateway build、PAPER-only account/session、client ID/next-valid-ID、callback drain、authoritative account/orders/positions/executions、disconnect/reconnect state、kill switch、broker egress 和 complete scenario receipt。任一不匹配保持 service stopped 或 new-risk closed。

## Controlled stop/restart

停止或重启前：

1. close new-risk gate / engage kill switch（PAPER）；
2. 停止生成新 proposal/permit；
3. 排空或持久化 authoritative events；
4. 保存 journal、snapshot、reconcile 和 binary/config identity；
5. 停止 Gateway admission，再停止 Execution；
6. restart 后从 Phase 1 重新验证，不继承旧 ready。

```bash
sudo systemctl stop hepta-tool-gateway.service
sudo systemctl stop hepta-execution-simulator.service
```

不要删除 state/journal、修改 command ID、替换单个 binary 或在 restart 后直接恢复风险增加。
