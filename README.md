# HeptaTrader

The [development index](docs/index.md) owns architecture, module contracts and operations.
For a broker-disabled native development build, run `./scripts/dev_core.sh`.
Python partitions use `python3 scripts/run_python_tests.py --lane core` or `--lane source`.

The simulator is the default development venue. IB PAPER still requires independent
exact-artifact qualification; LIVE is unavailable. Source checks never grant trading authority.
