# Iteration contract

Status: CURRENT  
Applies to: canonical source development

`./scripts/dev_core.sh` configures the default-disabled legacy/IB build, compiles the canonical core test targets, and runs all tests labeled `core`. The suite now includes the existing runtime coverage plus generic risk-boundary and fail-closed experimental-venue tests.

`python3 scripts/check_documentation.py` validates module/capability truth, and `python3 -m unittest discover -s tests/python -p 'test_*.py'` validates governance, documentation, qualification, and research source contracts.

Ordinary development CI never requires broker credentials. Repository source checks do not generate a PAPER receipt. Privileged GitHub governance and IB PAPER qualification are separate exact-artifact workflows, and missing external organization/runner/broker controls remain failed rather than being represented by local files.
