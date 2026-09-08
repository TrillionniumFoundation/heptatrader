# Deployment preflight

Status: CURRENT  
Applies to: immutable core and IB PAPER release packages

## Evidence boundary

`hepta-preflight` is read-only. It validates the package and selected static host properties, writes a machine-readable receipt, and exits non-zero on failure. It does not install files, change firewall rules, create users, deliver credentials, open sessions, disarm the kill switch, place orders, or authorize PAPER/LIVE.

## Artifact-only check

Run before an artifact reaches a privileged host:

```bash
hepta-preflight \
  --artifact /srv/releases/heptatrader-0.1.0-beta.1-core.tar.gz \
  --expected-sha256 <approved-package-sha256> \
  --profile core \
  --policy /usr/share/heptatrader/preflight-policy-v1.json \
  --artifact-only \
  --output /srv/releases/core-preflight.json
```

This checks the archive root, paths, types, size bounds, strict manifest, every payload digest/mode/timestamp, profile-required files, installed build metadata, and prohibition on authorization claims.

## Static host check

After installing the exact artifact into the canonical `/usr` tree, rerun without `--artifact-only`:

```bash
sudo hepta-preflight \
  --artifact /srv/releases/heptatrader-0.1.0-beta.1-core.tar.gz \
  --expected-sha256 <approved-package-sha256> \
  --profile core \
  --policy /usr/share/heptatrader/preflight-policy-v1.json \
  --output /var/lib/heptatrader/evidence/core-host-preflight.json
```

The static check validates Linux, required commands and the installed regular files represented by the package profile. It does not start services.

## IB PAPER static check

Provide the deployment-assigned numeric UIDs and the already engaged, root-owned kill-switch marker:

```bash
sudo hepta-preflight \
  --artifact /srv/releases/heptatrader-0.1.0-beta.1-ib-paper.tar.gz \
  --expected-sha256 <approved-package-sha256> \
  --profile ib-paper \
  --policy /usr/share/heptatrader/preflight-policy-v1.json \
  --execution-uid "$(id -u hepta-ib-execution)" \
  --gateway-uid "$(id -u hepta-tool-gateway)" \
  --kill-switch-path /etc/heptatrader/control/ib-paper.kill \
  --output /var/lib/heptatrader/evidence/ib-paper-host-preflight.json
```

A bounded TCP reachability check can be requested with `--probe-broker --broker-host 127.0.0.1 --broker-port 4002`. It is deliberately restricted to policy-approved loopback PAPER ports. TCP success is not evidence that the session is PAPER, the account is correct, callbacks are healthy, or mutations are qualified.

## Required follow-on checks

A PASS static receipt must be followed by the protected IB qualification workflow, which verifies the effective profile, credential boundary, broker session/account mode, authoritative refresh barriers, journal ordering, idempotency, cancel/recovery behavior and final reconciliation against the same artifact digest.

Any digest change, package replacement, host identity change, policy change, failed check, missing evidence or uncertain broker state invalidates the preflight for promotion. LIVE remains unavailable.
