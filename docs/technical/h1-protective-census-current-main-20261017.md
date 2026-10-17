# Pocket4 H1 stop diagnostics × latest Broker census

**2026-10-17. SOURCE-ONLY RESEARCH — NOT FOR PRODUCTION DEPLOYMENT.**

Original main `e2bd5db` (native protective-candidate lifecycle,
incremental OpenOrder/terminal 201/202 identity, account-global manual
permId) is integrated with reviewed H1 PR #408 research head `8bbd867`.
A new, independent Pocket4 Git worktree preserves the original user's
three dirty working-tree files, live Execution, FX/BTC drivers and OMS.

The only original merge conflict was the Adapter native C++ regression
test *invocation table*. Both sets of assertions were retained: main's
incremental foreign/manual STP candidate and foreign terminal
invalidations, **and** H1's original parent/STOP/permId/orderStatus,
post-fence and SDK wire negative checks. No previous test was removed.

Original IB SDK 10.30.01 + libbid, GNU C++ Release/Ninja, CMake 4.2.3
on Pocket4: **166/166 build steps** completed, including the entire
`hepta-ib-executiond` executable. SHA256:
`b3a09b93f59fe1c9d0f8034a14d6bc2a23cda68269e037a1f7e6ba1cb2be3090`.

Pocket4 test results on the exact same isolated combined source:
- H1/OMS/OCA, Coordinator, Broker census, real Adapter synthetic
  callbacks, PAPER profile and startup/quote/full runtime:
  **21/21 native CTests passed** in 43.42s.
- Python source membership/installation/systemd contracts:
  **116/116 tests passed** using a private executable TMPDIR.

Private Pocket4 evidence:
`~/.local/state/heptatrader/operator-evidence/h1-census-current-20261017-v1/`

- Native CTest log SHA256
  `1678797bac1d2d788a056b322a7b2744dff4d5723ad31ba5099d52ddf8b58923`.
- Python source tests log SHA256
  `4b5c6ea15a3bb1afe2bbe3d45ace75e31a1d6437b5b395155d7ae6345191214f`.

Original PAPER Execution stayed at PID 222401, cumulative
NRestarts=367, same InvocationID; FX/BTC drivers stayed active.
Native kill switch, durable operator hold and RefuseManualStop remain.
No real Broker order, cancel, new session, owner, model promotion,
or risk-policy change occurred in this isolated build.

**Strict limits.** IBKR PAPER stops and other complex orders are
[always simulated](https://www.interactivebrokers.com/campus/glossary-terms/paper-trading-account/).
A visible STP/GTC, OCA, permanent order ID or Submitted callback
is NOT independent coverage surviving Gateway/host/Driver death.
Keep `nativeStopBrokerWorkingProven`, `maySendOrdinaryExit`,
`mayStopDriver` and maintenance-stop permission false.

P0 #331 remains open pending actually qualified independent
PAPER risk-exit continuity and original-owner Broker lifecycle proof.
Never force-stop drivers or release the native/durable hold.
