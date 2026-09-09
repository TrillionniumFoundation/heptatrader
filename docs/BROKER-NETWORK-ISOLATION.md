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

## x230 qualification-host mapping

The bounded x230 qualification host intentionally runs the external Broker-owning process under a host-local principal rather than pretending that host UID `995` is the canonical UID `2003`.

`systemd/hepta-x230-paper-host-identity-map-v1.json` explicitly binds:

- logical execution identity `hepta-ib-exec:2003`;
- host execution identity `hepta-codex-ib:995`;
- Actions runner identity `hepta-actions-paper:994`;
- scope `ib-paper-qualification-only`;
- `live_authorized=false`.

The checkout-free `self-hosted-ib-availability.yml` workflow passes the exact SHA-256 of this reviewed map, both logical and host execution UIDs, and the actual runner UID to a separately pinned root-owned host probe. The runner must first prove that it cannot reach `127.0.0.1:4002`; the root probe then validates only non-secret host-boundary health against its root-owned host policy.

The repository's canonical policy remains keyed to logical UID `2003`. Any host-specific nftables policy that permits UID `995` is an external root-owned deployment input and must bind the reviewed identity-map digest. The workflow, map, or unit tests do not install that policy, grant credentials, open a kill switch, place an order, or qualify PAPER.

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
6. Missing/invalid policy or nft application failure attempts deny-all and returns failure.
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

The tests bind canonical policy UID `2003`, canonical service identity, the x230 logical-to-runtime map, its workflow-pinned digest, loopback-only rendering, runner denial, and deny-all behavior. They are source tests, not host or Broker evidence.
