# Host latency tuning research

Status: PROPOSAL  
Applies to: separately benchmarked IB PAPER hosts only

Host power plans, CPU affinity, process priority, core parking, NIC offloads, interrupt moderation, and colocation may affect latency and jitter, but they are not universal safe defaults and are not a release or authorization gate in the canonical repository.

Any host tuning must:

1. begin with a read-only inventory;
2. record hardware, kernel, firmware, NIC/driver, power, thermal, TWS/Gateway, and artifact versions;
3. benchmark baseline and candidate under controlled load;
4. measure percentiles, jitter, packet loss, callback lag, CPU starvation, and thermals;
5. apply one reversible change at a time;
6. retain rollback commands;
7. rerun core, network-boundary, reconnect, and bounded PAPER qualification tests.

Disabling flow control, interrupt moderation, energy features, or scheduler controls can reduce or worsen latency depending on hardware and load. No old PowerShell helper or local process-name check is considered canonical evidence. Deployment-specific tuning belongs in an independently reviewed host profile.

## Optional source connectivity probe

The optional root `BUILD_IB_PROBE=ON` build reuses `hepta_ibapi_client` rather
than carrying a second SDK source list. It requires `HEPTA_ENABLE_IBAPI=ON`, the
same explicit `IBAPI_ROOT` and pinned `IBAPI_DECIMAL_LIBRARY`; the root Decimal
ABI check remains mandatory. The probe derives its unused callbacks from the
pinned SDK's `DefaultEWrapper`. Compile-only verification does not establish
Broker connectivity, qualification or permission to trade. The probe is not
part of the default core package and is never launched by core tests.
