# PAPER campaign failure evidence and recovery

Status: CURRENT
Applies to: `scripts/run_ib_paper_artifact_qualification.sh`
Tests: `tests/python/test_ib_paper_campaign_retention.py`

The wrapper reserves a mode-0700 evidence directory before invoking the pinned external harness. It durably writes `campaign-start.json`, then writes `campaign-status.json` on normal or trapped exit. A nonzero harness exit, missing result, timeout, TERM or HUP retains raw evidence and reports failure. SIGKILL cannot run a finalizer: an attempt with only a start record remains incomplete. The host must retain the evidence volume independently of the lifetime of the Actions runner.

The final directory is also the workflow upload path; successful publication is no longer a prerequisite for failure evidence to exist. Secret-bearing HOME scratch is outside that directory and is removed on trapped exits. The trusted harness must export only non-secret evidence. Untrappable termination can leave private scratch for protected operator cleanup, never public upload.

An existing evidence directory rejects a new campaign invocation. Neither an incomplete marker nor a completed wrapper status is trading authorization. Both receipts carry `authorization_effect=NONE`, `paper_authorized=false` and `live_authorized=false`. Only the existing exact-artifact qualification verifier can validate complete Broker-observed evidence.

After interruption, first stop new risk and establish authoritative Broker reconciliation under the protected operator procedure. Preserve the candidate, harness, profile and original command identities. When all required result and effect evidence already exists, invoke `verify_ib_paper_qualification.py` on that retained evidence and a fresh no-replace verification receipt path; this read-only verification does not rerun the harness. Missing Broker evidence cannot be fabricated or repaired by replaying mutations. A new campaign requires a separately authorized attempt after the previous possible sends have been resolved.

The regression suite executes the real wrapper with local dummy programs only. It covers success, partial failure, missing result, shortened GNU timeout, signal forwarding, evidence-path conflicts, secret scratch separation and refusal to execute either a failed or successful attempt twice. These tests do not establish real Broker qualification.
