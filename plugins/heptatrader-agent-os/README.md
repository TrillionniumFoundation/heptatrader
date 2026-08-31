# HeptaTrader Agent OS Plugin

Status: current entrypoint
Applies to: plugin packaging and local MCP navigation only
Verification: exact-revision core and install gates
Authority: entrypoint only; canonical authority is the identity/capability contract

The canonical security and capability boundary is [`../../docs/contracts/IDENTITY-CAPABILITY-CONTRACT.md`](../../docs/contracts/IDENTITY-CAPABILITY-CONTRACT.md).

This plugin exposes the local Tool Gateway through MCP. It contains no broker credential, account secret, PAPER/LIVE grant or execution authority. Each mutually untrusted Agent must use an independent OS identity, socket, session token and capability set. Final risk, permit, journal, OMS, reconciliation and venue mutation remain Execution Authority responsibilities.
