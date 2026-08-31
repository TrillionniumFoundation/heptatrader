# HeptaTrader Research Package

Status: current
Applies to: `research/` executable package and static manifest
Verification: `python3 research/run_protocol.py verify --manifest research/manifest-v1.json`
Authority: research package entry point

`research/` 是 capability-free、deterministic 的研究与回放实现。它只产生 `RunManifest`、`EventLog`、`RunSummary` 及 StrategyProposal 所需的离线经济信息，不能建立 session、持有 Broker credential、生成 runtime permit 或授权 PAPER/LIVE。

权威协议见 [`../docs/research/RESEARCH-PROTOCOL.md`](../docs/research/RESEARCH-PROTOCOL.md)，策略门禁见 [`../docs/research/STRATEGY-VALIDATION.md`](../docs/research/STRATEGY-VALIDATION.md)。

```bash
python3 research/run_protocol.py verify --manifest research/manifest-v1.json
python3 research/run_protocol.py self-test
```

同一 source/data/config bytes、seed 和 numeric policy 必须产生相同 canonical output digest。研究有效不等于 runtime promotion。
