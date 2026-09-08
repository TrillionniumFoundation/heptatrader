# QMT SDK research note

Status: PROPOSAL  
Applies to: future XT/QMT integration

A prior review observed a Python `xtquant` package and local binary extensions on one Windows installation. That observation is insufficient to define a supported, redistributable, or reproducible SDK boundary.

Before implementation, retain and review:

- product and SDK version identifiers;
- vendor documentation and license/redistribution terms;
- package, extension, and dependency digests;
- supported Python and OS/architecture matrix;
- callback threading and shutdown semantics;
- account/order/trade identity guarantees;
- reconnect, duplicate callback, and partial-fill behavior;
- sandbox, credential, IPC, and upgrade policy.

No developer-specific installation path is canonical. The current C++ scaffold has no transport and remains fail-closed. See [`XTQMT-VENUE-PLAN.md`](XTQMT-VENUE-PLAN.md).
