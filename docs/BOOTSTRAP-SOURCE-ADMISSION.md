# Trusted default-branch source bootstrap

Status: CURRENT  
Applies to: remediation pull request 37 only

The repository began this remediation with an unprotected `main` branch and no active ruleset. To prevent the candidate branch from merging itself, `.github/workflows/bootstrap-pr37-source-admission.yml` was committed directly to the pre-candidate default branch as a bounded bootstrap harness.

The harness runs candidate code only in a job with `contents: read` and a credential-free checkout. A separate job with write permission does not checkout or execute candidate bytes. It re-reads the open pull request, requires the exact validated head, waits for the independently executed source-admission, GCC/Clang ASan/UBSan and ThreadSanitizer jobs, and then performs an SHA-locked squash merge.

This bootstrap establishes source admission, not repository-governance qualification. It is restricted to pull request 37, the same-repository remediation branch and the exact event head. It does not close `G-TEAM-001`, create organization teams, activate a ruleset, protect an environment, assign runners, or authorize PAPER/LIVE trading.

After the source candidate reaches `main`, governance bootstrap must remove or archive this one-shot workflow through the newly protected review path. Its existence and run record remain part of the audit trail explaining the transition from the initially unprotected repository state.
