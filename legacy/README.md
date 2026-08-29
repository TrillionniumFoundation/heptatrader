# Legacy source surface

This directory preserves the pre-Agent-runtime monolith, historical strategy framework, legacy simulator, vendor-facing interface tree and Visual Studio solutions.

It is intentionally outside the active root build and runtime install graph. New execution, risk, Agent, strategy-intent or venue work must not add dependencies from the active runtime back into this directory.

Legacy code may be used for reference or isolated migration only. It has no current capability status, no PAPER/LIVE authority, and is not covered by the default core gate unless an explicitly scoped migration adds dedicated tests.
