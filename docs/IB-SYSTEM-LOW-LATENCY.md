# IB host latency and reliability checklist

低延迟优化不能优先于正确性、隔离和可恢复性。仓库不再提供修改 Windows power plan、NIC 或进程优先级的一键脚本；此类宿主变更由独立运维基线管理，并在隔离 PAPER 环境测量后批准。

## Required measurements

- Gateway 与 Execution 的时钟同步和 callback latency；
- quote age、request-to-send、send-to-ack、ack-to-fill、reconcile duration；
- reconnect 次数、1100/1101/1102、201 和 outcome-uncertain；
- CPU saturation、scheduler delay、page faults、disk sync latency 和 journal queue depth；
- 同一 commit/config/dataset 的 p50/p95/p99 与最坏值。

## Safe tuning order

1. 保证 IB Gateway 仅监听 loopback PAPER 端口，并验证 nftables egress policy；
2. 固定 CPU/内存/磁盘资源和时间同步；
3. 采集未调优基线；
4. 一次只修改一个 host setting；
5. 运行 deterministic replay、fault injection 和受控 PAPER read-only；
6. 只有 tail latency 改善且错误率/温度/稳定性无回退时保留变更。

不要使用 realtime priority、关闭安全机制或放宽 systemd sandbox 来换取延迟。任何调优不得改变 order state machine、risk、journal durability、socket identity 或 reconciliation 语义。结果必须进入受控 qualification evidence，而不是提交开发机专属命令。
