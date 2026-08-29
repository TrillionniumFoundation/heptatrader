# CTP 6.5.1 legacy Tools overlay

The four content-addressed runtime libraries described by `manifest-v1.json`
remain in the legacy `Tools/` tree.  Embedded vendor version strings identify
them as CTP 6.5.1 (`v6.5.1_20200908 10:25:08`).

This overlay is distinct from the disabled-experimental CTP 6.7.7 SDK under
`Interface/CTPTradeApi*` and `third_party/ctp/6.7.7`.  Paths, digests, versions,
and platform identities from the two overlays must never be merged or
substituted.

The original download source and a redistributable license are not retained.
Only this metadata enters the source-only bundle.  The `Tools/` payload remains
local, nonredistributable, and outside the Agent OS product closure.
