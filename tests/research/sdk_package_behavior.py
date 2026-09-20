#!/usr/bin/env python3
"""Install, relocate, build and run a real offline SDK consumer. No stubs or skips."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


CONSUMER = r'''
#include <hepta/research/market_data.h>
#include <hepta/research/analytics.h>
#include <hepta/research/replay.h>
#include <hepta/research/strategy.h>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <sstream>
#include <vector>
using namespace hepta::research;
static void Require(bool value) {
    if (!value) throw std::runtime_error("installed SDK contract failed");
}
int main() {
    SessionWindow window; window.openUs = 0; window.closeUs = 100; window.tradingDay = "20260920";
    SessionSchedule schedule(std::vector<SessionWindow>(1, window));
    BarBuilder builder("TEST.FUT", 10, schedule);
    Tick tick; tick.instrument = "TEST.FUT"; tick.timestampUs = 1; tick.sequence = 1; tick.price = 100; tick.volume = 2;
    Bar first, second;
    Require(builder.Push(tick, first) == TickOutcome::Updated);
    tick.timestampUs = 11; tick.sequence = 2; tick.price = 102; tick.volume = 3;
    Require(builder.Push(tick, first) == TickOutcome::ClosedPrevious);
    Require(first.complete && first.close == 100 && first.volume == 2);
    Require(builder.AdvanceWatermark(20, second));
    BarSeries series(3); series.Append(first); series.Append(second);
    Require(series.MeanClose(2) == 101 && series.Highest(0, 1) == 1);
    Require(series.ConfirmedPeaks(0, 1, 1).empty());
    Bar third = second; third.beginUs = 20; third.endUs = 30;
    third.open = third.high = third.low = third.close = 101; series.Append(third);
    const auto peaks = series.ConfirmedPeaks(0, 2, 1, BarPrice::Close);
    Require(peaks.size() == 1 && peaks[0].index == 1 && peaks[0].confirmedAtUs == 30);
    Require(series.AtLatest().close == 101 && series.RetainedBarsInLatestTradingDay() == 3);
    Require(series.Highest(0, 2, BarPrice::Open) == 1 && series.Lowest(0, 2, BarPrice::Close) == 0);
    Require(series.LatestHigher(100, 0, 2).index == 2 && series.LatestLower(101, 0, 2).index == 0);
    std::ostringstream barCsv; WriteBarsCsv(barCsv, {first, second, third});
    std::istringstream barInput(barCsv.str()); const auto restored = ReadBarsCsv(barInput);
    Require(restored.size() == 3 && restored[2].close == 101 && restored[2].endUs == 30);
    MovingAverageForecast strategy(1, 2); Forecast forecast;
    Require(!strategy.OnCompletedBar(first, forecast));
    Require(strategy.OnCompletedBar(second, forecast) && forecast.direction == 1);
    ResearchLedger ledger("TEST.FUT", 1000, 10);
    ResearchFill fill; fill.fillId = "fill-1"; fill.orderId = "order-1"; fill.instrument = "TEST.FUT";
    fill.timestampUs = 12; fill.side = 1; fill.quantity = 2; fill.price = 100; fill.fee = 1;
    Require(ledger.Apply(fill) && !ledger.Apply(fill));
    Require(ledger.Mark(102).equity == 1039);
    ReplayMatcher matcher("TEST.FUT", schedule);
    ReplayOrder order; order.orderId = "order-2"; order.instrument = "TEST.FUT"; order.tradingDay = "20260920";
    order.submittedAtUs = 12; order.expiresAtUs = 80; order.side = 1; order.quantity = 2; order.limitPrice = 103;
    Require(matcher.Submit(order));
    tick.timestampUs = 12; tick.sequence = 1; tick.volume = 1;
    Require(matcher.OnTick(tick).empty());
    tick.timestampUs = 13; tick.sequence = 2;
    const auto events = matcher.OnTick(tick);
    Require(events.size() == 1 && events[0].kind == ReplayEventKind::Fill && events[0].remaining == 1);
    const auto expiry = matcher.AdvanceWatermark(80);
    Require(expiry.size() == 1 && expiry[0].kind == ReplayEventKind::Expired);
    matcher.Finish(80); Require(matcher.Finished() && matcher.ActiveOrders() == 0);
    std::vector<EquityPoint> points(3);
    points[0].equity = 1000;
    points[1].timestampUs = 1; points[1].equity = 1100;
    points[2].timestampUs = 2; points[2].equity = 1100; points[2].externalFlow = 100;
    Require(std::fabs(EvaluateEquity(points, 252).totalReturn) < 1e-12);
    // Exercise the corrected Data and Analytics symbols after relocation,
    // not a separately compiled fragment of their source implementation.
    const double maximum = std::numeric_limits<double>::max();
    BarSeries extremeSeries(7);
    MovingAverageForecast extremeStrategy(3, 7);
    for (int i = 0; i < 32; ++i) {
        Bar bar = first; bar.beginUs = i * 10; bar.endUs = (i + 1) * 10;
        bar.open = bar.high = bar.low = bar.close = maximum;
        extremeSeries.Append(bar);
        Require(extremeSeries.MeanClose(extremeSeries.Size()) == maximum);
        Require(!extremeStrategy.OnCompletedBar(bar, forecast));
    }
    ResearchLedger extremeLedger("TEST.FUT", 1000, 1);
    fill.quantity = 1; fill.price = maximum; fill.fee = 0;
    for (int i = 1; i <= 4096; ++i) {
        fill.fillId = "extreme-" + std::to_string(i); fill.timestampUs = i;
        Require(extremeLedger.Apply(fill) && !extremeLedger.Apply(fill));
    }
    const auto extremeAccount = extremeLedger.Mark(maximum);
    Require(extremeAccount.quantity == 4096 && extremeAccount.averageEntry == maximum &&
            extremeAccount.unrealized == 0 && extremeAccount.equity == 1000);
    std::cout << "installed research contract passed\n";
}
'''
CMAKE = '''cmake_minimum_required(VERSION 3.16)
project(ResearchSDKConsumer LANGUAGES CXX)
find_package(HeptaResearch CONFIG REQUIRED COMPONENTS Data Analytics Replay Strategy)
if(TARGET HeptaResearch::NativeClient OR TARGET hepta_native_tool_client)
    message(FATAL_ERROR "offline package exported a native authority dependency")
endif()
add_executable(consumer main.cpp)
target_compile_features(consumer PRIVATE cxx_std_11)
set_target_properties(consumer PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_link_libraries(consumer PRIVATE HeptaResearch::Replay HeptaResearch::Strategy)
'''


def run(command: list[str], *, success: bool = True) -> str:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=90, check=False)
    if (result.returncode == 0) != success:
        raise RuntimeError(f"unexpected exit={result.returncode}: {command!r}\n{result.stdout}")
    return result.stdout


def cache_values(build: Path) -> dict[str, str]:
    values = {}
    for line in (build / "CMakeCache.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.split(":", 1)[0]] = value
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cmake", required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--config", default="Release")
    args = parser.parse_args()
    build, source = args.build_dir.resolve(), args.source_dir.resolve()
    cache = cache_values(build)
    config = args.config or cache.get("CMAKE_BUILD_TYPE") or "Release"
    upper = config.upper()
    # Never let a caller's DESTDIR redirect installation outside the temp tree.
    if os.environ.get("DESTDIR"):
        raise RuntimeError("installation acceptance requires an unset DESTDIR")
    with tempfile.TemporaryDirectory(prefix="hepta research package ") as temporary:
        root = Path(temporary)
        prefix = root / "original prefix"
        relocated = root / "relocated prefix"
        run([args.cmake, "--install", str(build), "--config", config,
             "--prefix", str(prefix), "--component", "ResearchSDK"])
        if not prefix.is_dir():
            raise RuntimeError("SDK component installed no files")
        shutil.move(str(prefix), str(relocated))
        if prefix.exists():
            raise RuntimeError("original prefix still exists")
        configs = list(relocated.rglob("HeptaResearchConfig.cmake"))
        if len(configs) != 1:
            raise RuntimeError("expected one installed package configuration")
        package_dir = configs[0].parent
        expected_headers = {"market_data.h", "analytics.h", "replay.h", "strategy.h"}
        headers = list(relocated.rglob("*.h"))
        if {p.name for p in headers} != expected_headers or len(headers) != 4:
            raise RuntimeError("SDK header allowlist differs or leaked native headers")
        forbidden = (str(source), str(build), str(prefix), "hepta_native_tool_client", "NativeStrategyClient")
        for path in relocated.rglob("*.cmake"):
            text = path.read_text(encoding="utf-8")
            if any(value in text for value in forbidden):
                raise RuntimeError(f"non-relocatable or privileged export: {path.name}")
        infos = list(relocated.rglob("sdk-build-info.txt"))
        if len(infos) != 1 or "native_gateway_or_broker_authority=none" not in infos[0].read_text():
            raise RuntimeError("missing SDK scope/build metadata")
        consumer = root / "consumer source"
        consumer.mkdir()
        (consumer / "main.cpp").write_text(CONSUMER, encoding="utf-8")
        (consumer / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        consumer_build = root / "consumer build"
        common = [f"-DHeptaResearch_DIR={package_dir}",
                  "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
                  f"-DCMAKE_BUILD_TYPE={config}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
        for name in ("CMAKE_CXX_COMPILER", "CMAKE_CXX_FLAGS", f"CMAKE_CXX_FLAGS_{upper}",
                     "CMAKE_EXE_LINKER_FLAGS", f"CMAKE_EXE_LINKER_FLAGS_{upper}"):
            if name in cache:
                common.append(f"-D{name}={cache[name]}")
        run([args.cmake, "-S", str(consumer), "-B", str(consumer_build), *common])
        run([args.cmake, "--build", str(consumer_build), "--config", config, "--parallel", "2"])
        executable = consumer_build / ("consumer.exe" if os.name == "nt" else "consumer")
        if not executable.exists():
            executable = consumer_build / config / executable.name
        if "installed research contract passed" not in run([str(executable)]):
            raise RuntimeError("consumer did not exercise the installed libraries")
        commands = (consumer_build / "compile_commands.json").read_text(encoding="utf-8")
        # Account for JSON's escaping of backslashes and quotes.
        commands = json.dumps(json.loads(commands), ensure_ascii=False)
        if str(source) in commands or str(build) in commands or str(prefix) in commands:
            raise RuntimeError("consumer compiled against original source/build/staging paths")
        replay_names = {"hepta-research-replay", "hepta-research-replay.exe"}
        replay = [p for p in relocated.rglob("*") if p.is_file() and p.name in replay_names]
        ticks = list(relocated.rglob("ticks.csv")); sessions = list(relocated.rglob("sessions.csv"))
        if len(replay) != 1 or len(ticks) != 1 or len(sessions) != 1:
            raise RuntimeError("installed CLI/examples are incomplete")
        output = run([str(replay[0]), str(ticks[0]), str(sessions[0]), "TEST.FUT", "10", "1", "2", "1"])
        summary = json.loads(output.splitlines()[-1])
        if summary.get("broker_authorized") is not False or summary.get("finalized") is not True or summary.get("active_orders") != 0:
            raise RuntimeError("installed replay did not retain its offline terminal contract")
        # Missing capabilities and mismatching versions must not become stubs.
        for name, request, expected in (
            ("native", "find_package(HeptaResearch CONFIG REQUIRED COMPONENTS NativeClient)", "NativeClient"),
            ("wrong-version", "find_package(HeptaResearch 999.0.0 EXACT CONFIG REQUIRED)", "999.0.0"),
        ):
            negative = root / name; negative.mkdir()
            (negative / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\nproject(NegativeConsumer LANGUAGES CXX)\n" + request + "\n",
                encoding="utf-8")
            output = run([args.cmake, "-S", str(negative), "-B", str(root / (name + " build")), *common], success=False)
            if expected not in output:
                raise RuntimeError("negative configure failed for an unrelated reason: " + output)
        print("SDK_INSTALL_ACCEPTANCE_PASS: relocation, external linkage, CLI, component and version rejection")


if __name__ == "__main__":
    main()
