# CTP 6.7.7 Vendor Boundary

The headers in `include/` are a separately controlled, ignored overlay and are
the single byte-preserving canonical copy used by all legacy platform include
paths. A source checkout intentionally does not contain that directory;
`converge_ctp_vendor_headers.py --check-forwarders-only` verifies the
redistributable forwarding surface, while `--check` and the full vendor verifier
require an operator-provided overlay whose bytes match `manifest-v1.json`.
Platform binaries remain under
`Interface/CTPTradeApi32`, `Interface/CTPTradeApi64`, and
`Interface/CTPTradeApiLinux` because their architecture-specific payloads are
not interchangeable.

The three legacy `version.txt` payloads identify this import as CTP 6.7.7.
Their hashes and the platform-specific PE/ELF architecture checks are part of
`manifest-v1.json` and the repository governance gate.

The original download URL and a separately redistributable license file were
not preserved by the legacy import. The manifest therefore keeps distribution
authorization false and marks license/origin review as required. The complete
vendor root is denied by default in distributable clean-source bundles; the
only explicit exceptions are this README and the content-addressed manifest.
No future sibling payload becomes distributable merely by appearing below the
version directory. A missing overlay is therefore not repaired from the
network and cannot silently downgrade into a stub compile. CTP remains a
disabled experimental venue and is not part of the Agent OS runtime component.
