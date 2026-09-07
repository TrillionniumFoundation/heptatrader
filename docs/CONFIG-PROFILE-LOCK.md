# Configuration source and profile lock

Status: LEGACY  
Applies to: old monolith/PowerShell launch path

The historical resolver unified XML and environment inputs for `sim|paper|live`. The canonical runtime now uses per-service immutable configuration and an explicit capability matrix: simulator is current, IB PAPER is qualification-required, CTP/XT are experimental with no transport, and LIVE is unavailable.

The durable principles remain valid: one source per process, conflict rejection, configuration digesting, no template fallback for broker operation, and no secret in repository configuration. Current deployment details are in [`operations/configure.md`](operations/configure.md).
