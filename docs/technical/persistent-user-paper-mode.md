# Persistent user PAPER mode: opt-in source capability

The current tmpfs profile is unchanged. A new explicitly selected user profile
may use the OS-passwd-resolved `~/.local/state/heptatrader/operator-control`.
No arbitrary path or HOME override is accepted. The owner/group, mode 0750 and
directory inode remain checked and pinned. No current installation is migrated.

In this profile native admission reads `operator-mode`: a bounded owner/group
regular file, mode 0440, one link, no symlinks. Exact contents are `HOLD\n` or
`PAPER_ENABLED\n`. Missing mode means HOLD rather than implicit permission.
Malformed/unreadable/changing state is uncertain. Read-only daemon operation is
not prevented by a missing mode file; increasing risk is denied. The independent
kill-switch marker always overrides PAPER_ENABLED. Existing authorized cancel
and reduce-only checks remain separate and unchanged; uncertain state retains
its original refusal behavior. PAPER_ENABLED removes only this hold, not identity,
order, market, financial-risk or model-selection requirements.

Mode changes use a private new file, exact mode, file fsync, atomic rename and
parent fsync. No automatic release, order method or service-control API is added.
Selecting this directory is an actual profile/deployment change. Current Pocket4
masks, engaged stop and tmpfs rearm helper remain untouched. Source tests are not
permission to migrate, restart, unmask or trade.

Unit tests exercise absent/HOLD/enabled states, marker precedence, independent
process reopen, atomic replacement, malformed values, unsafe permissions, links
and FIFO. Reopen is not an actual reboot or physical power-loss test.
