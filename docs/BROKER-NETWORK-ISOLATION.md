# Broker network isolation

Status: CURRENT  
Applies to: canonical IB PAPER deployment and the bounded x230 qualification host

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

Every path component is opened through pinned `O_DIRECTORY|O_NOFOLLOW` descriptors. The policy leaf is acquired with `O_NOFOLLOW|O_NONBLOCK`, must be a root-owned, root-group, mode-`0644`, regular single-link file on the parent filesystem, and is read under a compiled byte ceiling. The helper rechecks descriptor identity, final path identity and a freshly reopened parent chain after the read, then verifies the exact compiled digest and semantic tuple before installing any allow rule. A symlink, writable ancestor, alternate path, owner/mode mismatch, in-place mutation, leaf replacement, parent substitution, digest mismatch, schema mismatch or nft failure is a failed activation.

The failure path is independent of the untrusted policy contents: it attempts the compiled deny-all ruleset for the four protected loopback ports and returns failure. Explicit `--deny-all` also uses only the compiled tuple and never reads the policy file. If both the requested activation and deny-all fallback fail, the service reports both failures and cannot be treated as ready.

## x230 qualification-host mapping

The bounded x230 qualification host intentionally runs the external Broker-owning process under a host-local principal rather than pretending that host UID `995` is the canonical UID `2003`.

`systemd/hepta-x230-paper-host-identity-map-v1.json` explicitly binds:

- logical execution identity `hepta-ib-exec:2003`;
- host execution identity `hepta-codex-ib:995`;
- Actions runner identity `hepta-actions-paper:994`;
- scope `ib-paper-qualification-only`;
- `live_authorized=false`.

The checkout-free `self-hosted-ib-availability.yml` workflow passes the exact SHA-256 of this reviewed map, both logical and host execution UIDs, and the actual runner UID to a separately pinned root-owned host probe. The runner must first prove that it cannot reach `127.0.0.1:4002`; the root probe then validates only non-secret host-boundary health against its root-owned host policy.

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
python3 -m unittest discover -s tests/python -p 'test_hepta_broker_egress_policy.py'
python3 -m unittest discover -s tests/python -p 'test_self_hosted_ib_availability.py'
```

The tests bind the installed canonical policy path and digest, root-owned descriptor admission, mutation and namespace-swap rejection, canonical UID `2003`, service identity, the x230 logical-to-runtime map, loopback-only rendering, runner denial, apply-failure fallback and explicit deny-all independence. They are source tests, not host or Broker evidence.
