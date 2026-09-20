#!/usr/bin/env python3
"""Install, relocate and consume the real watermark API and converter."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CONSUMER = r'''#include <hepta/research/market_data.hpp>
#include <stdexcept>
using namespace hepta::research;
int main() {
    BarBuilder b("TEST", {{0,10,"20260921"},{20,30,"20260921"}},0,FirstVolume::BaselineOnly);
    Bar out;
    b.Push({"TEST","20260921",0,10,1,100},out);
    b.Push({"TEST","20260921",9,-2,2,105},out);
    if (b.AdvanceWatermark(10,out)) return 1;
    if (!b.AdvanceWatermark(30,out) || !out.complete || out.endUs!=30 ||
        out.volume!=5 || out.ticks!=2 || out.close!=-2) return 2;
    if (b.Push({"TEST","20260921",9,-2,2,105},out)!=PushResult::Duplicate) return 3;
    try { b.Push({"TEST","20260921",20,4,3,107},out); return 4; }
    catch (const std::invalid_argument&) {}
    if (b.Finish(out)) return 5;
    return 0;
}
'''


def run(command, cwd, env):
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                               text=True, timeout=120, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {command[0]}\n"
                           f"{completed.stdout}\n{completed.stderr}")
    return completed.stdout


def validate(args):
    build = args.build_dir.resolve(strict=True)
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "CMAKE_PREFIX_PATH", "CMAKE_MODULE_PATH"):
        env.pop(name, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="hepta-watermark-install-") as directory:
        root = Path(directory)
        original, prefix = root / "original", root / "relocated-sdk"
        command = ["cmake", "--install", str(build), "--prefix", str(original)]
        if args.config:
            command += ["--config", args.config]
        run(command, root, env)
        original.rename(prefix)
        configs = list(prefix.rglob("HeptaResearchConfig.cmake"))
        if len(configs) != 1 or original.exists():
            raise RuntimeError("relocated SDK/config missing or original prefix retained")
        for config in configs[0].parent.glob("*.cmake"):
            text = config.read_text()
            if any(value in text for value in (str(original), str(build), str(Path(__file__).resolve().parents[2]))):
                raise RuntimeError("installed package leaks a source/build/old-prefix dependency")
        if not (prefix / args.datadir / "doc/hepta-research/WATERMARK.md").is_file():
            raise RuntimeError("watermark contract was not installed")
        source, external = root / "consumer", root / "consumer-build"
        source.mkdir()
        (source / "main.cpp").write_text(CONSUMER)
        (source / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.16)
project(WatermarkConsumer LANGUAGES CXX)
find_package(HeptaResearch CONFIG REQUIRED)
add_executable(consumer main.cpp)
target_link_libraries(consumer PRIVATE Hepta::ResearchData)
''')
        run(["cmake", "-S", str(source), "-B", str(external),
             f"-DCMAKE_PREFIX_PATH={prefix}", f"-DCMAKE_CXX_COMPILER={args.cxx}",
             f"-DCMAKE_CXX_FLAGS={args.flags}", f"-DCMAKE_EXE_LINKER_FLAGS={args.flags}",
             f"-DCMAKE_BUILD_TYPE={args.config or 'Release'}"], root, env)
        run(["cmake", "--build", str(external), "--parallel", "2"], root, env)
        consumer = external / ("consumer.exe" if os.name == "nt" else "consumer")
        run([str(consumer)], root, env)
        binary = prefix / args.bindir / ("hepta-research-bars.exe" if os.name == "nt" else "hepta-research-bars")
        if not binary.is_file():
            raise RuntimeError("installed converter missing")
        env["HEPTA_RESEARCH_BARS"] = str(binary)
        # The test code may remain in the checkout; all code under test comes
        # from the relocated binary/library, not from a staged substitute.
        output = run([sys.executable, "-I", "-B", "-X", "dev", "-W", "error",
                      str(Path(__file__).with_name("test_watermark.py")), "-v"], root, env)
        print(output, end="")
    print("PASS actual CMake install, relocation, external watermark API and installed converter")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--config", default="Release")
    parser.add_argument("--bindir", default="bin")
    parser.add_argument("--datadir", default="share")
    parser.add_argument("--cxx", default="c++")
    parser.add_argument("--flags", default="")
    args = parser.parse_args()
    try:
        validate(args)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"watermark installation validation FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
