# Research data, strategy planning and offline evaluation

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `research`
Tests: `tests/research/market_data_tests.cpp`, `tests/research/test_csv.py`, `tests/research/test_model.py`

## Responsibilities and dependencies

[The research contract](../../research/README.md) owns input schemas, public APIs,
build commands, source provenance and the complete feature migration matrix.
`hepta_research_data` is a C++11 SDK-free static library; `hepta_research_bars` is
its concrete CSV consumer. Both compile through the root CMake graph. The core
test aggregate builds the consumer as well as its test executable. The Python
model depends only on the standard library. No broker ports, credentials or
Gateway/Execution implementation libraries are dependencies.

## Failure and state model

A malformed/off-session/conflicting tick is rejected without advancing the bar
state. The stream is single-writer and single-instrument; session data are explicit
inputs, not a current trading-calendar assertion. EOF is an incomplete bar. CSV
consumers must discard staged output on nonzero exit. Research balances are
hypothetical and never become authoritative runtime snapshots. Planning refuses
stale/incomplete position projections and outstanding orders. Replay has explicit
next-bar timing and execution costs; undefined performance ratios remain null.

## Tests and operational boundary

C++ tests and Python/CLI behavior tests run in the existing core CTest lane and
in isolated GCC/Clang/sanitizer research CI. This is a selected responsibility
port, not complete Pegasus matching or HeptaDLL ABI compatibility. No private
history/SDK is imported and no legacy runtime switch is reopened. Research
results confer neither PAPER nor LIVE authorization.
