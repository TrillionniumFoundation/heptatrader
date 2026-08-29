# Legacy source surface

This directory preserves the pre-Agent-runtime monolith, historical strategy framework, simulator, vendor-facing interface tree, old Visual Studio projects/configuration and retired direct-order strategy helpers.

It is intentionally outside the active build, runtime install and documentation capability graph. New execution, risk, Agent, strategy-intent or venue work must not add a dependency from active code into this directory.

`legacy/monolith/HeptaTrade/` contains files that formerly sat beside the current runtime but were not compiled by the active CMake graph: the old demo trader, direct multi-strategy implementation, JSONL bridge, watchdog, monolith configuration/instrument tables and the superseded pre-trade risk engine.

Legacy code is reference-only or an input to an explicitly isolated migration. It has no current capability status, no PAPER/LIVE authority and no coverage claim from the default core gate.
