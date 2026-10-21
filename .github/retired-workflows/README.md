# Retired Desktop workflow contracts

The two Desktop-only Actions entry points were retired on 2026-10-20.
GitHub Actions does not discover workflows in this directory. Their YAML bytes,
qualification ordering, immutable artifact inputs and executable shell fixtures
are retained so historical evidence and contract checks remain reproducible.

- `ib-paper-qualification.yml`: the former Desktop builder / PAPER qualification protocol.
- `self-hosted-ib-availability.yml`: the former checkout-free Desktop host probe.

The current Pocket4 development and status entries are documented in
[the iteration contract](../../docs/ITERATION.md). Reusing a template on another
host requires an explicit new host-bound configuration and its own qualification;
moving it here grants no order, model-promotion or service-stop permission.

Historical artifacts use the verifier and trusted source from their original
frozen revision. New builds retain exact trusted-file membership and hashes,
using the archived workflow's actual path. No mixed old/new allowlist is added.

Run the archived shell and trust-boundary fixtures with
`python3 scripts/run_python_tests.py --lane legacy`.
[Archived qualification contracts](../workflows/legacy-qualification.yml) runs this
lane only when its archived inputs, verifier, runner or tests change, and also
supports manual dispatch. The fixtures use disposable stubs; the retired broker
workflow itself is not reactivated.
