# Exact-head verification interpretation

Status: current
Applies to: interpretation of HeptaTrader pull-request checks and canonical gap states
Verification: the permanent GitHub Actions `core` check on the exact pull-request head

The authoritative merge-readiness signal is the successful, permanent, read-only
`core` check attached to the current pull-request head SHA. A `canonical-full-suite`
check records the broader static, Python, research, Release C++, CTest, installation,
sanitizer, crash/replay, malformed-protocol, and performance verification performed
on its source parent. This document-only child is then rechecked by `core`, so the
complete source tree and the final documentation state are both covered without
retaining a write-enabled workflow in the proposed tree.

A failed, cancelled, stale, or missing same-head `core` result overrides prose and
reopens the corresponding PLAN item. A check created manually without executing
the named commands is not sufficient. The final tree must contain no `dev-*`
workflow and no permanent workflow granting `contents: write`.

Ready for review means the implementation and verification gates are satisfied. It
does not constitute review approval, venue certification, LIVE activation, or
permission to merge. Those remain separate governance decisions.
