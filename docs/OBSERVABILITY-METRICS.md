# Observability metrics

`hepta-observability` 是只读 OMS journal collector。它不连接 Gateway、Execution socket 或 Broker，不具备 mutation capability。collector 只读取当前 service UID 拥有（或 root 拥有）、mode 不宽于 `0600`、非 symlink、大小受限且读取期间 identity 稳定的 journal。

## Outputs

```bash
/usr/libexec/hepta-observability \
  --journal /var/lib/hepta-execution/oms-journal.jsonl \
  --metrics-output /var/lib/hepta-execution/heptatrader.prom \
  --alerts-output /var/lib/hepta-execution/heptatrader-alerts.json
```

输出通过同目录临时文件、`fsync` 和原子替换写入，mode 为 `0600`。Simulator 和 IB PAPER 分别由对应 systemd timer 周期刷新。存在 P1 时默认仍成功写出快照；人工/CI 检查可加 `--fail-on-p1`。

## Metrics

- `heptatrader_oms_events_total{event}`：按事件类型计数；label 长度有界。
- `heptatrader_oms_risk_blocks_total{risk_code}`：按稳定风控代码计数。
- `heptatrader_oms_broker_errors_total{code}`：按 Broker 错误码计数。
- `heptatrader_oms_malformed_lines_total`：非法或超大记录数。
- `heptatrader_oms_duplicate_event_ids_total`：重复事件 ID 数。
- `heptatrader_oms_outcome_uncertain_total`：需要 reconciliation 的不确定结果数。
- `heptatrader_oms_active_orders`：由每个 order 最新非终态记录推导的活动订单数。
- `heptatrader_oms_last_event_timestamp_seconds`：最新事件 epoch 秒。

这些是 journal 派生指标，不替代 daemon 内部 queue depth、sync latency 或 live connection health。未来增加直接 exporter 时必须保持只读权限和有界 cardinality。

## Collection failure

文件 owner/mode/path 不安全、读取期间被替换、超过大小上限或输出目录可被非 owner 写入时 collector fail closed，不发布新快照。值班人员应检查 journal、磁盘、service UID 和潜在路径篡改，而不是放宽权限。
