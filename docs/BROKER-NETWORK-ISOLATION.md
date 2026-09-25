# Broker network isolation

Status: CURRENT  
Applies to: canonical desktop IB PAPER deployment; retained X230 mapping is compatibility-only

## 目标

Agent 能生成并运行代码，因此“没有 broker credential”还不够。Agent、Tool Gateway 和 Actions runner 也不能直接连接本地 broker API 端口；broker 网络可达性必须只属于经过审阅的 broker-owning Execution identity。

## Canonical deployment boundary

Canonical systemd deployment uses:

- `scripts/hepta_broker_egress_policy.py`;
- `systemd/hepta-broker-egress-policy.service`;
- `systemd/hepta-broker-network-policy-v1.json`;
- logical IB PAPER identity `hepta-ib-exec` / UID `2003` from `systemd/hepta-service-identities-v1.json`.

The source-controlled nftables policy authorizes logical UID `2003` and rejects every other local UID for the protected loopback ports. The rules match IPv4/IPv6 loopback destinations only; an unrelated remote service using the same destination port is not affected. Other egress is unchanged.

The canonical install places the policy at the exact non-relaxable path `/usr/share/heptatrader/hepta-broker-network-policy-v1.json`. The privileged helper accepts no alternative path. Its compiled SHA-256 is `5eddd44a588ac3269804cb62adb19c3879febce8569df30ab86886028e969e6b`, and its compiled semantic tuple is `inet/hepta_broker_egress_v1/output`, protected ports `4001,4002,7496,7497`, authorized UID `2003`.

Every path component is opened through pinned `O_DIRECTORY|O_NOFOLLOW` descriptors. The policy leaf is acquired with `O_NOFOLLOW|O_NONBLOCK`, must be a root-owned, root-group, mode-`0644`, regular single-link file on the parent filesystem, and is read under a compiled byte ceiling. The helper compares two bounded reads from the same pinned descriptor, so a same-size modification within one filesystem timestamp tick cannot hide behind metadata equality. It then rechecks descriptor identity, final path identity and a freshly reopened parent chain, and verifies the exact compiled digest and semantic tuple before installing any allow rule. This validates the accepted snapshot; it does not freeze a privileged writer after return. A symlink, writable ancestor, alternate path, owner/mode mismatch, in-place mutation, leaf replacement, parent substitution, digest mismatch, schema mismatch or nft failure is a failed activation.

The failure path is independent of the untrusted policy contents: it attempts the compiled deny-all ruleset for the four protected loopback ports and returns failure. Explicit `--deny-all` also uses only the compiled tuple and never reads the policy file. If both the requested activation and deny-all fallback fail, the service reports both failures and cannot be treated as ready.

Table existence and final policy state are determined from `nft -j` machine output, never localized stderr text. Each attempt renders one deterministic create-or-replace transaction from the observed table state; present/absent races are retried only within a compiled bound. Activation or deny-all succeeds only after structural JSON readback proves the exact table, output chain, hook priority/policy and complete commented IPv4/IPv6 rule set with no extra permissive rule.

## Desktop qualification route

The active PAPER workflows select `desktop-ib-paper` in runner group
`trillionnium-ib-paper`, with the `heptatrader-ib-paper` and `desktop-ib-paper`
labels. Candidate construction checks the existing `desktop-ib-builder` role;
it does not inherit the qualifier's credentials or direct Broker access.
The campaign controller requires `127.0.0.1:4002` explicitly and forwards it to
the pinned harness. The host must verify that this is its approved PAPER session.

The checkout-free availability job passes logical and runtime execution UID
`2003` and the actual runner UID to the pinned root-owned host probe. It first
requires the runner's direct Broker connection to fail. The probe must verify
the canonical root-owned desktop policy; it must not accept the supplied UID
as authorization. No X230 UID remapping is used on the desktop.

Registering the dedicated desktop qualifier, restricting its runner-group
access, installing the matching root-owned host probe/harness and their digest
variables are host administration. A queued workflow or a listening port proves
none of those conditions. There is no fallback to the generic `desktop` runner
or to the no-secret builder when the qualifier is unavailable.

## Retained X230 qualification-host mapping

The bounded x230 qualification host intentionally runs the external Broker-owning process under a host-local principal rather than pretending that host UID `995` is the canonical UID `2003`.

`systemd/hepta-x230-paper-host-identity-map-v1.json` explicitly binds:

- logical execution identity `hepta-ib-exec:2003`;
- host execution identity `hepta-codex-ib:995`;
- Actions runner identity `hepta-actions-paper:994`;
- scope `ib-paper-qualification-only`;
- `live_authorized=false`.

Historical X230 qualification used this map with its separately pinned host probe. The current desktop workflow does not pass this map or select an X230 runner. The retained mapping remains available only for interpreting that distinct host boundary; it is not desktop readiness evidence.

The repository's canonical policy remains keyed to logical UID `2003`. Any host-specific nftables policy that permits UID `995` is an external root-owned deployment input and must bind the reviewed identity-map digest. It is not accepted by the canonical service helper merely by changing `--policy`. The workflow, map, or unit tests do not install that policy, grant credentials, open a kill switch, place an order, or qualify PAPER.

## 当前受保护端口

- `4001`
- `4002`
- `7496`
- `7497`

These are common local TWS/IB Gateway API listeners. Rules require a loopback destination as well as a protected destination port.

## Invariants

1. Agent、Tool Gateway 和 Actions runner 不能连接受保护 broker API 端口。
2. Canonical deployment only permits logical `hepta-ib-exec:2003`.
3. The x230 host may permit only the mapped execution UID `995`; runner UID `994` remains denied.
4. Simulator identity does not inherit broker network access.
5. All trading mutation enters Broker authority through the local typed Execution protocol.
6. Missing, substituted, mutated or invalid policy and any nft application failure attempt the compiled deny-all boundary and return failure.
7. Network isolation does not replace pre-trade risk, kill switch, journal, reconciliation, credentials, or Broker qualification.
8. No mapping or reachability result authorizes LIVE.

## Lifecycle

`hepta-execution-ib-paper.service` directly depends on `hepta-broker-egress-policy.service`. Canonical startup loads the logical-identity policy before PAPER authority; stopping the policy service tightens the table to deny-all.

A qualification host must validate its separate root-owned mapping and effective host policy before the external harness obtains Broker access. Repository code never rewrites the canonical policy from an Actions job.

## Verification

```bash
python3 -m unittest tests.python.test_hepta_broker_egress_policy tests.python.test_hepta_broker_egress_policy_atomic -v
python3 -m unittest discover -s tests/python -p 'test_self_hosted_ib_availability.py'
```

The tests bind the installed canonical policy path and digest, root-owned descriptor admission, mutation and namespace-swap rejection, canonical UID `2003`, service identity, the x230 logical-to-runtime map, loopback-only rendering, runner denial, apply-failure fallback and explicit deny-all independence. They are source tests, not host or Broker evidence.
