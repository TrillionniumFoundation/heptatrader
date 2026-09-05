# HeptaTrader Research Package

Status: current entrypoint
Applies to: `research/` package navigation only
Verification: `python3 research/run_protocol.py verify --manifest research/manifest-v1.json`
Authority: entrypoint only; canonical authority is `docs/research/`

The canonical research contract is [`../docs/research/RESEARCH-PROTOCOL.md`](../docs/research/RESEARCH-PROTOCOL.md). Strategy validation and promotion rules are linked from that document.

```bash
python3 research/run_protocol.py verify --manifest research/manifest-v1.json
python3 research/run_protocol.py self-test
```

The package is deterministic and capability-free. It cannot establish a broker session, hold credentials, create runtime permits, or grant PAPER/LIVE authority.
