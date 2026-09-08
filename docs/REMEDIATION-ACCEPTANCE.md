# HeptaTrader blocker-remediation acceptance criteria

Status: CURRENT  
Applies to: the canonical source candidate and external qualification sequence

## Source candidate

A source candidate is acceptable only when one exact Git revision satisfies all of the following without modifying its checkout:

- every catalogued module has an implementation-grade document and existing implementation/test ownership;
- fresh CMake File API output agrees with the committed Core build inventory;
- the canonical optimized C++ suite and the full Python contract suite pass;
- GCC and Clang ASan/UBSan suites pass and the dedicated ThreadSanitizer suite reports no race;
- protocol generated bindings are byte-for-byte current and deterministic parser/property tests pass;
- the SDK-free install tree and component packages contain the intended runtime and no IB execution binary, credential, private key or unsafe symlink;
- repository-controlled gap state is derived from those exact-SHA checks rather than copied from an editable status string;
- no one-shot migration controller, export workflow, duplicate policy template or administration probe remains in the product tree.

Passing this section establishes source correctness only. It does not authorize a broker connection.

## Repository governance

`G-TEAM-001` closes only after authenticated GitHub readback proves that the exact default branch is covered by active no-bypass protection, required check contexts bind the expected GitHub Actions workflow/job provenance, CODEOWNERS teams exist and have the required access and staffing, protected environments prevent self-approval, and a genuine merge-group revision succeeds. Policy JSON, administrator permission, a direct push, screenshots or a manually authored receipt are not substitutes.

The governance verifier must consume live paginated API data from a trusted default-branch workflow and issue an immutable receipt for the effective state. Any missing page, skipped check, stale approval, excluded default ref or bypass reopens the gap.

## IB PAPER qualification

`G-IB-001` closes only after the protected workflow admits the unchanged source candidate, builds an immutable broker binary with the pinned IB SDK and Intel BID archive on the no-secret builder, independently verifies the artifact, and runs the bounded PAPER campaign on the assigned host and account. The campaign must exercise rejection, reconnect, uncertain-send recovery without blind retry, callback duplication/order changes, partial fill, cancel race, replay and final guarded flatten, ending in authoritative flat/clean state.

A simulator run, compile-only run, historical operator note or unprotected broker session is not qualification. The trusted verifier must bind source, binary, SDK, harness, profile, builder, runner, environment, account mode and terminal evidence in the final receipt. PAPER qualification never grants LIVE authority.

## Authorization projection

Until both external receipts validate for the exact admitted revision, `paper_authorized` remains false. `live_authorized` remains false unconditionally because LIVE is outside the current capability contract. Source improvements may reduce external work, but cannot manufacture organization, runner or broker facts.

## Cleanup and rollback

Temporary workflows are deleted in the same verified commit that materializes their outputs. A failed migration leaves its controller visible and therefore fails source acceptance. Rollback preserves journals, leases, terminal witnesses and receipt provenance; it may not synthesize an empty state to make a prior binary start.
