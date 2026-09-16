# CTP 6.7.7 external vendor boundary

CTP remains an **external, operator-supplied SDK input**. The repository does not
ship CTP headers or platform libraries and does not reconstruct them from the
network.

`manifest-v1.json` records the byte-exact CTP 6.7.7 headers expected by a future
adapter build. An operator-provided overlay may place those headers under
`third_party/ctp/6.7.7/include/`; the directory is intentionally absent from a
clean checkout. Any future CTP-linked CMake target must verify those bytes and
must also require an explicit platform trader/market-data library path. It may
not search retired repository locations for a fallback SDK.

The historical repository previously carried platform payloads below
`Interface/CTPTradeApi32`, `Interface/CTPTradeApi64` and
`Interface/CTPTradeApiLinux`. Those trees were retired with the legacy runtime
and are **not** current vendor inputs. Their old content remains recoverable from
Git history; it is deliberately not duplicated in this active manifest or in a
new archive.

The original vendor download URL and a separately redistributable license file
were not preserved by the legacy import. Distribution authorization therefore
remains false and license/origin review remains required before any owner
chooses a new external SDK package. Possession of matching headers or libraries
does not authorize a CTP connection or order mutation.

The implementable adapter state/correlation/recovery contract is
[`docs/technical/ctp-adapter-contract.md`](../../../docs/technical/ctp-adapter-contract.md).
Until that implementation and its external qualification exist, CTP remains a
disabled experimental venue and the checked-in adapter must fail closed.
