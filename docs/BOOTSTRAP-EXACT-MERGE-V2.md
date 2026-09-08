# Trusted exact-merge trigger V2

Status: CURRENT  
Applies to: pull request 37 exact-head bootstrap

This audit marker triggers the idempotent trusted-main merge workflow after its installation. The workflow accepts only the same-repository remediation branch, re-reads the exact PR head, requires the read-only trusted validation plus source admission, GCC and Clang ASan/UBSan, and ThreadSanitizer success, and submits an SHA-locked squash merge. It does not change PAPER or LIVE authorization and is retained only to make the bootstrap transition explicit in Git history.
