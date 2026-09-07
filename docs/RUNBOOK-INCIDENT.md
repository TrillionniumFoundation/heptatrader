# Incident runbook compatibility entry

Status: CURRENT  
Applies to: canonical runtime

The maintained incident procedure is [`operations/incident.md`](operations/incident.md). Old PowerShell CI-gate and log-summary commands are not canonical recovery evidence.

For a possible order, position, journal, identity, credential, kill-switch, or broker-state ambiguity: engage the operator kill switch, stop new risk, fence sessions, preserve exact evidence, query authoritative state, and use only guarded cancel/reduce/flatten paths. Never retry an uncertain mutation with a new command ID or delete state to make startup succeed.
