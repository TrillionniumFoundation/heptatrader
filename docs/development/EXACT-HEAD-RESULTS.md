# Exact-head verification interpretation

Status: current
Applies to: interpretation of HeptaTrader pull-request checks and canonical gap states
Verification: the permanent GitHub Actions `canonical-full-suite` and `core-runtime` checks on the exact pull-request head

The authoritative merge-readiness signal is the successful, permanent, read-only
`canonical-full-suite` check attached to the current pull-request head SHA. The
fast `core-runtime` check is an early feedback signal. The full-suite check
records the broader static, Python, research, Release C++, CTest, installation,
sanitizer, crash/replay, malformed-protocol, and performance verification on
that same SHA. The document-only child is then rechecked by `core-runtime`, so
the complete source tree and final documentation state are both covered without
retaining a write-enabled workflow in the proposed tree.

A failed, cancelled, stale, or missing same-head `canonical-full-suite` result
overrides prose and reopens the corresponding PLAN item. The fast `core-runtime`
check must also pass for the documentation child. A check created manually
without executing the named commands is not sufficient. The final tree must contain no `dev-*`
workflow and no permanent workflow granting `contents: write`.

Ready for review means the implementation and verification gates are satisfied. It
does not constitute review approval, venue certification, LIVE activation, or
permission to merge. Those remain separate governance decisions.
