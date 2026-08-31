# XT/QMT to Hepta semantic mapping

This document is a design map, not an enabled adapter. `${QMT_HOME}` denotes an operator-provided, licensed SDK installation. The repository does not ship or load it in the default build.

| XT/QMT concept | Canonical Hepta meaning |
|---|---|
| connect / account subscribe | connection epoch plus authenticated account binding |
| asset / position callbacks | authoritative snapshot records with explicit completion |
| order request | typed Execution mutation after risk and durable intent journal |
| asynchronous order response | send/acceptance evidence, not a fill or terminal state |
| order callback | lifecycle projection correlated by service-owned ID |
| trade callback | execution/fill evidence with quantity and price validation |
| cancel response/callback | cancel attempt and later authoritative terminal evidence |
| error callback | structured reject/error event preserving vendor code and detail |

A synchronous return or async response must never be translated directly into `filled`, and a locally generated ID must not masquerade as a Broker order ID. Callback duplication, partial fill, reconnect and cancel race require explicit state-machine tests.

Current `adapter_xt` has no transport and returns `XT_TRANSPORT_UNAVAILABLE` for every outbound operation. Only after transport isolation, authoritative reconciliation, common risk/journal/fencing integration and controlled PAPER qualification may this mapping become an implemented venue.
