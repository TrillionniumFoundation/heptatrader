# Reviewed local prebuilt overlay

This directory contains metadata only.  The payloads described by
`manifest-v1.json` remain at their legacy `Interface/` paths so existing local
worktrees are not broken, but they are not part of the Agent OS source
distribution.

The eight payloads are needed only by legacy Windows support, the deprecated
monolith, or the deprecated Pegasus simulator.  None is required by
`hepta-tool-gatewayd`, `hepta-executiond`, `hepta-ib-executiond`, the native
client SDK, or their tests.

Every payload is denied from the default source bundle because the original
package/rebuild provenance is incomplete.  TinyXML's source notice and the
repository's own license text are recorded as evidence, but they do not prove
that these particular archive bytes were reproducibly built.  The IB runtime
files and BID archive have no retained redistributable license file.

An internal user may provide the exact content-addressed files as a separate
local overlay after reviewing the applicable licenses.  Such an overlay is not
a HeptaTrader release artifact and must never be merged with the source-only
bundle.
