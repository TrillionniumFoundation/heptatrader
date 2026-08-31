# Startup runbook

本手册覆盖默认 Simulator 安装以及受控 IB PAPER 安装。命令以 root/operator 身份执行；Agent 不得拥有这些权限。

## 1. 验证制品

```bash
sha256sum -c SHA256SUMS
python3 /path/to/verify_install_tree.py --root /usr
```

确认版本、commit provenance、SBOM 和 capability matrix 与预期一致。默认 core package 不应出现 `hepta-ib-executiond`。

## 2. 创建身份与私有配置

按照 `systemd/hepta-service-identities-v1.json` 创建固定非 root 身份。把 `.env.example` 复制到 `/etc/heptatrader/` 后仅填写非 secret 配置；secret 使用 systemd credentials。实际 UID 必须与 env、socket owner、trust-domain policy 和 execution context binding 完全一致。

运行 tmpfiles 创建受控目录：

```bash
systemd-tmpfiles --create /usr/lib/tmpfiles.d/heptatrader-agent-os.conf
```

IB PAPER 还需要 `heptatrader-ib-paper.conf`、合法 control directory、execution fence、PAPER authorization、FX cash baseline 以及已经应用的 Broker egress policy。

## 3. Simulator 启动

```bash
systemctl daemon-reload
systemctl start hepta-execution-simulator.socket
systemctl start hepta-execution-events-simulator.socket
systemctl start hepta-execution-simulator.service
systemctl start hepta-tool-session-supervisor.socket
systemctl start hepta-tool-gateway.socket
systemctl start hepta-tool-gateway.service
systemctl enable --now hepta-observability-simulator.timer
```

检查：

```bash
systemctl --no-pager --full status \
  hepta-execution-simulator.service \
  hepta-tool-gateway.service
journalctl -u hepta-execution-simulator.service -u hepta-tool-gateway.service --since -10min
```

然后由 operator 使用 `hepta-sessionctl` provision 一个受限 session，再通过只读工具查询健康状态。没有 session token 时不得临时放宽 socket mode。

## 4. IB PAPER 启动

只有在 `docs/PROD-GO-LIVE-CHECKLIST.md` 的 PAPER 项全部通过后执行：

```bash
systemctl daemon-reload
systemctl start hepta-broker-egress-policy.service
systemctl start hepta-execution-ib-paper.socket
systemctl start hepta-execution-events-ib-paper.socket
systemctl start hepta-execution-ib-paper.service
systemctl enable --now hepta-observability-ib-paper.timer
```

先以 kill switch engaged 或只读 session 验证连接、账户、authoritative positions、open orders、journal 和 reconciliation。不得以启动成功替代 Broker qualification。

## 5. Fail-closed criteria

以下任一情况立即保持或重新 engage kill switch，并停止新增风险：

- UID/GID、credential、socket、policy 或路径不匹配；
- journal/alerts 显示 malformed、duplicate ID、outcome uncertain 或 projection failure；
- Broker connection epoch、account、position 或 open-order snapshot 不确定；
- 资格认证证据不属于当前 commit；
- systemd sandbox 或 egress policy 未生效。
