#!/usr/bin/env python3
"""Exercise an installed and relocated SDK, never a broker or privileged runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {command[0]}\n"
                           f"{result.stdout}\n{result.stderr}")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.stdout


def require(condition: bool, description: str) -> None:
    if not condition:
        raise RuntimeError(description)


def validate(build: Path, cxx: str, flags: str, source_sha: str | None = None) -> dict:
    if source_sha is not None and not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA must be an exact commit identifier")
    build = build.resolve(strict=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    checks = []
    with tempfile.TemporaryDirectory(prefix="hepta-installed-research-") as directory:
        root = Path(directory)
        original, prefix = root / "original", root / "relocated-sdk"
        run(["cmake", "--install", str(build), "--prefix", str(original)], cwd=root, env=env)
        original.rename(prefix)
        require(not original.exists(), "old install prefix still exists")
        configs = list(prefix.rglob("HeptaResearchConfig.cmake"))
        require(len(configs) == 1, "one exported package configuration required")
        for config in configs[0].parent.glob("*.cmake"):
            text = config.read_text()
            for forbidden in (str(original), str(build), str(Path(__file__).resolve().parents[2])):
                require(forbidden not in text, "installed export leaks a build/source path")
        checks.append("relocated_export_has_no_source_or_old_prefix_dependency")

        consumer = root / "consumer"
        consumer.mkdir()
        (consumer / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.16)
project(ResearchConsumer LANGUAGES CXX)
find_package(HeptaResearch CONFIG REQUIRED)
add_executable(consumer main.cpp)
target_link_libraries(consumer PRIVATE Hepta::ResearchData)
''')
        (consumer / "main.cpp").write_text('''#include <hepta/research/market_data.hpp>
#include <iostream>
using namespace hepta::research;
int main() {
    if (CivilDay(2000,1,1).Serial()!=10957) return 1;
    BarBuilder builder("TEST", {{0,30,"20260921"}},10,FirstVolume::BaselineOnly);
    Bar out;
    if (builder.Push({"TEST","20260921",0,100,1,10},out)!=PushResult::Buffered) return 2;
    if (builder.Push({"TEST","20260921",9,102,2,13},out)!=PushResult::Buffered) return 3;
    if (builder.Push({"TEST","20260921",10,101,3,15},out)!=PushResult::ClosedBar) return 4;
    if (out.open!=100 || out.close!=102 || out.volume!=3 || !out.complete) return 5;
    if (!builder.Finish(out) || out.complete || out.volume!=2) return 6;
    std::cout << "external C++ consumer PASS\\n";
}
''')
        external_build = root / "consumer-build"
        run(["cmake", "-S", str(consumer), "-B", str(external_build),
             "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_CXX_COMPILER={cxx}",
             f"-DCMAKE_CXX_FLAGS={flags}", f"-DCMAKE_EXE_LINKER_FLAGS={flags}",
             f"-DCMAKE_PREFIX_PATH={prefix}"], cwd=root, env=env)
        run(["cmake", "--build", str(external_build), "--parallel", "2"], cwd=root, env=env)
        require("PASS" in run([str(external_build / "consumer")], cwd=root, env=env),
                "external C++ consumer did not complete")
        checks.append("external_cpp_consumer_links_and_runs_installed_static_library")

        bars_tool = prefix / "bin/hepta-research-bars"
        launcher = prefix / "bin/hepta-research-replay"
        require(bars_tool.is_file() and launcher.is_file(), "installed commands missing")
        sessions, ticks, bars, report_path = (root / name for name in (
            "sessions.csv", "ticks.csv", "bars.csv", "report.json"))
        sessions.write_text("begin_us,end_us,trading_day\n0,40,20260921\n")
        ticks.write_text("instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume\n"
                         "TEST,20260921,0,1,100,10\nTEST,20260921,10,2,102,20\n"
                         "TEST,20260921,20,3,104,30\nTEST,20260921,30,4,103,40\n")
        # Capture first, publish the converter output only after successful exit.
        normalized = run([str(bars_tool), str(ticks), str(sessions), "10", "baseline"], cwd=root, env=env)
        bars.write_text(normalized)
        checks.append("installed_cpp_converter_consumes_explicit_synthetic_data")
        run([sys.executable, "-I", str(launcher), "--bars", str(bars), "--output", str(report_path),
             "--tick-size", "1", "--capital", "1000", "--quantity", "2", "--fast", "1",
             "--slow", "2", "--slippage", "1", "--fee-per-unit", "0.5"], cwd=root, env=env)
        report = json.loads(report_path.read_text())
        require(report["mode"] == "OFFLINE_HYPOTHETICAL" and
                report["assumptions"]["broker_authorized"] is False, "offline boundary missing")
        require(report["input"]["bars_sha256"] == hashlib.sha256(bars.read_bytes()).hexdigest(),
                "input bytes do not match report digest")
        require(len(report["fills"]) == 1 and report["fills"][0] ==
                {"timestamp_us": 20, "delta": "2", "price": "105", "fee": "1.0"},
                "unexpected next-bar fill/cost")
        require([item["value"] for item in report["equity"]] == ["1000", "1000", "997.0", "995.0"],
                "unexpected equity accounting")
        require(report["position"] == "2" and report["pending_target"] is None and
                report["equity"][-1]["complete"] is False, "incomplete final bar must not signal")
        checks.append("installed_python_launcher_runs_causal_report_without_pythonpath")
        python_root = prefix / "share/heptatrader/research/python"
        probe = ("import pathlib,sys; sys.path.insert(0,sys.argv[1]); "
                 "import hepta_research.pipeline as p; import hepta_research.gateway as g; "
                 "root=pathlib.Path(sys.argv[1]).resolve(); "
                 "assert pathlib.Path(p.__file__).resolve().is_relative_to(root); "
                 "assert pathlib.Path(g.__file__).resolve().is_relative_to(root); "
                 "print('installed imports PASS')")
        require("PASS" in run([sys.executable, "-I", "-c", probe, str(python_root)], cwd=root, env=env),
                "Python import must resolve to relocated install")
        checks.append("installed_compute_and_gateway_modules_import_without_checkout")
        digests = {str(path.relative_to(prefix)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (bars_tool, launcher, python_root / "hepta_research/pipeline.py")}
    return {"schema": "hepta.research.install-validation.v1", "status": "PASS",
            "source_sha": source_sha, "compiler": cxx, "flags": flags,
            "checks": checks, "installed_sha256": digests,
            "broker_qualification": False, "canonical_runtime_qualification": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--cxx", default="c++")
    parser.add_argument("--flags", default="")
    parser.add_argument("--source-sha")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    # Never leave a previous PASS report as evidence of this failed invocation.
    if args.report is not None:
        args.report.unlink(missing_ok=True)
    try:
        result = validate(args.build_dir, args.cxx, args.flags, args.source_sha)
        text = json.dumps(result, indent=2, sort_keys=True)+"\n"
        if args.report is not None:
            args.report.write_text(text)
        print(text, end="")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"installed research validation FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
