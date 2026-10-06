# Single-main convergence — 2026-10-06

This is a historical source disposition, not trading deployment qualification.
All listed tips are retained as ancestors of main; there is one maintained tree.
A verified pre-change Git bundle is retained in the private operator evidence.

| Former branch | Tip | Content disposition |
|---|---|---|
| `docs/freeze-heptadll-dossier-20260928` | `e73d7e5ce4e15646423a9415e3a433a2fff5ca27` | Current implementation merged with later production fixes retained |
| `feat/account-net-pnl-20261006` | `0ad2a1fec885426ebb6517a14ed133c46b58d038` | Current implementation merged with later production fixes retained |
| `feat/cost-aware-entry-20261006` | `b3a96d5b0da408d2af2f2489722fc190ce8b21e3` | Current implementation merged with later production fixes retained |
| `feat/native-0dte-20261005` | `a56266967b581caf60c9510cabe6ad3cdb552721` | Current implementation merged with later production fixes retained |
| `feat/user-owned-paper-20260928` | `d384e98c3a52485bfba97cd93cd89197d9872fa0` | Current implementation merged with later production fixes retained |
| `fix/ib-global-upgrade-notice-20261006` | `b3c629470e088a38d7918a5509e7cbcbcfb6125e` | Current implementation merged with later production fixes retained |
| `fix/partial-fill-cash-reconcile-20261006` | `387a1ed0373e298d0c38c37999ddc3975f8dfc37` | Current implementation merged with later production fixes retained |
| `fix/pnl-accounting-20261006` | `523bbcf89cc67fa17ef1f0d305bd0dc4c2c418f3` | Current implementation merged with later production fixes retained |
| `fix/required-check-reachability-20260928` | `5fbd5dfbb6395b349acba458921e6654407600ed` | Superseded admission history retained; obsolete polling gate not restored |
| `fix/startup-cash-diagnostic-hold-20261006` | `cce996d9574653730b33af2fd2ebeb0b332d7768` | Current implementation merged with later production fixes retained |
| `integration/heptadll-modular-20260919` | `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5` | Historical ancestry retained only; prior canonical ports and retired-runtime decisions preserved |
| `integration/heptadll-modular-20260921` | `56fd92bc94fd36e064d18c383ffeef9994d85fea` | Historical ancestry retained only; prior canonical ports and retired-runtime decisions preserved |
| `integration/heptadll-modular-20260922` | `7e36806fbe326086c25b01a33583d9f5db4239ba` | Historical ancestry retained only; prior canonical ports and retired-runtime decisions preserved |
| `main` | `9c31f88de078152539e9fa87ab1a43e41104c650` | Fast-forward ancestor of consolidated main; no history rewrite |
| `refactor/coordinator-test-split-20260928` | `bc8d0e8f883c922b4dabd899e8fde239845039ce` | Current implementation merged with later production fixes retained |

## Conflict resolutions

The later cumulative fill, partial-cancel and fee/PnL fixes win over the old
options aggregate. The old aggregate class was removed and its assertions moved
to the single production cumulative-evidence engine. The coordinator compilation
split retains all 35 current test functions, including three later cash-order/
correlation regressions. No test case was dropped to make the merge compile.

The old integration branches have unrelated Git ancestry and had already been
selectively ported and retired in [HeptaDLL consolidation](heptadll-consolidation.md).
Their commits are joined with an explicitly history-only merge rather than
restoring alternative OMS, direct-SPI, ABI or model implementations.

## Development simplification

Normal changes run one native/Python CI job. Cross-workflow approval polling and
duplicate workflow-ID policies were removed; metadata/header/index synchronization
was replaced by local link checking. Deep sanitizer and SDK integration are manual.
Actual installed-artifact release and external IB qualification remain separate.

This operation does not install main into the trading runtime, enable unqualified
options or cost cohorts, modify pending orders, reset accounting or change risk
limits. Operational qualification remains required for a later deployment.
