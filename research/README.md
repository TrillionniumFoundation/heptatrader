# Hepta research modules

Explicit C++11 data, analytics, offline replay and completed-bar strategy APIs,
plus a separately linked adapter to the existing NativeToolClient. These are
experimental developer components, not a restored HeptaDLL runtime or a
source/ABI-compatible replacement. The offline libraries also have a standalone
installable SDK; the native integration remains a separate root-build boundary.

From the repository root:

```sh
cmake -S research -B build/research -DCMAKE_BUILD_TYPE=Release
cmake --build build/research --parallel 2
ctest --test-dir build/research --output-on-failure
build/research/hepta-research-replay research/examples/ticks.csv research/examples/sessions.csv TEST.FUT 10 1 2 1
cmake --install build/research --prefix "$PWD/stage/research-sdk" --component ResearchSDK
```

The CSV files are synthetic. The example has no gateway address, credentials or
broker linkage and is not a profitability claim. The root build adds the native
client and real local Gateway fixture tests; standalone research does not.
Standalone CTest additionally installs, relocates and consumes the real SDK from
an external C++11 project. Root production packaging is unchanged.

See [SDK installation and consumption](PACKAGE.md),
[module contracts](../docs/modules/research-sdk.md) and the
[integration/provenance record](../docs/technical/heptadll-integration.md).

## Strategy observation time

The existing `BarStrategy` now has a nonvirtual `ObserveCompletedBar(bar,
observedAtUs, forecast)` entry. Use it when a closed bar is delivered later than
its period boundary, including historical replay gaps and delayed data feeds.
The observation must be in the bar's UTC-microsecond domain and at or after
`bar.endUs`. Invalid or incomplete input is rejected before the existing
`OnCompletedBar` callback is invoked. On emission, the callback must return the
same instrument and direction -1, 0 or +1; the published timestamp is the supplied
observation time. Suppression or failure leaves the caller's output unchanged.

```cpp
hepta::research::MovingAverageForecast calculation(5, 20);
hepta::research::BarStrategy& strategy = calculation;
hepta::research::Forecast forecast;
// completed is a validated closed bar; deliveryUtcUs is actual availability.
if (strategy.ObserveCompletedBar(completed, deliveryUtcUs, forecast)) {
    // Consume the forecast as research input, NOT an authorized broker order.
}
```

The original calculation callback, object layout and virtual table are retained.
Its bar-end output alone does not prove actual availability. The caller still
owns delivery sequencing: this stateless wrapper does not infer a wall clock,
reorder data, undo arbitrary callback state, or provide durable strategy replay.
A callback that throws or emits invalid output must be handled by its owner;
only forecast publication, not arbitrary user code, has an all-or-nothing bound.

The existing offline CLI calls the observation entry after `ReplayMatcher`
accepts each tick. It submits at that observation, and the unchanged matcher
rejects clock reversal and any same-timestamp fill. No new runtime, live state,
permit, client transport, order path or broker capability is introduced. The
existing replay test compares 30,720 delivered observations against independent
integer-sum forecasts and tests delayed/no-signal/invalid callbacks. The existing
installed/relocated C++11 consumer also links and executes this public method.
These are synthetic research tests, not a broker or latency qualification.
