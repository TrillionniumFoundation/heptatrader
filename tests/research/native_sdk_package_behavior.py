#!/usr/bin/env python3
"""Install/relocate the real forward-only SDK and rebuild its existing consumer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

# Keep acceptance inputs pristine even when the caller does not set the env flag.
sys.dont_write_bytecode = True
from sdk_package_behavior import cache_values, run


CMAKE = '''cmake_minimum_required(VERSION 3.16)
project(StrategyClientConsumer LANGUAGES CXX)
find_package(HeptaStrategyClient CONFIG REQUIRED COMPONENTS Client)
foreach(forbidden hepta_agent_os_core hepta_execution_core hepta_execution_server
                  HeptaStrategyClient::Execution HeptaStrategyClient::Gateway)
    if(TARGET ${forbidden})
        message(FATAL_ERROR "Privileged SDK target: ${forbidden}")
    endif()
endforeach()
foreach(name Client NativeToolClient ToolProtocol UnixToolClient)
    get_target_property(links HeptaStrategyClient::${name} INTERFACE_LINK_LIBRARIES)
    if(links AND NOT links STREQUAL "links-NOTFOUND")
        foreach(link IN LISTS links)
            if(NOT link MATCHES "^(HeptaStrategyClient::(NativeToolClient|ToolProtocol|UnixToolClient)|Threads::Threads)$")
                message(FATAL_ERROR "Unreviewed client link dependency: ${link}")
            endif()
        endforeach()
    endif()
endforeach()
add_executable(consumer main.cpp)
set_target_properties(consumer PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
# CXX_STANDARD alone permits compiler extensions. Make the external C++11
# consumer reject newer-language constructs instead of accepting them silently.
if(CMAKE_CXX_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$")
    target_compile_options(consumer PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
target_link_libraries(consumer PRIVATE HeptaStrategyClient::Client)
'''
HEADERS = {
    "client/native_tool_client.h", "client/native_tool_discovery_contract.h",
    "execution/trading_contract.h", "tool_host/trading_tool_request.h",
    "tool_host/typed_tool_protocol.h", "tools/trading_tool_types.h",
    "tools/trading_tool_wire_contract.h", "hepta/research/native_strategy_client.h",
}
LIBRARIES = {"libhepta_research_native_client.a", "libhepta_native_tool_client.a",
             "libhepta_typed_tool_protocol.a", "libhepta_unix_tool_client.a"}


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
    if os.environ.get("DESTDIR"):
        raise RuntimeError("acceptance requires an unset DESTDIR")
    with tempfile.TemporaryDirectory(prefix="hepta client package ") as temporary:
        root = Path(temporary)
        prefix, relocated = root / "original prefix", root / "relocated prefix"
        # Execute the generated subdirectory install script with NO component.
        # SDK exclusion is tested by actual installation, not a token search.
        default = root / "ordinary install"
        run([args.cmake, "--install", str(build / "research"), "--config", config,
             "--prefix", str(default)])
        if default.exists() and any(default.rglob("*")):
            raise RuntimeError("default installation unexpectedly included the client SDK")
        run([args.cmake, "--install", str(build), "--config", config,
             "--prefix", str(prefix), "--component", "StrategyClientSDK"])
        if not prefix.is_dir():
            raise RuntimeError("client SDK component installed no files")
        shutil.move(str(prefix), str(relocated))
        if prefix.exists():
            raise RuntimeError("original install prefix still exists")
        header_roots = [p for p in relocated.rglob("HeptaStrategyClient")
                        if (p / "client/native_tool_client.h").is_file()]
        if len(header_roots) != 1:
            raise RuntimeError("missing or ambiguous public include namespace")
        includes = header_roots[0]
        actual_headers = {p.relative_to(includes).as_posix() for p in includes.rglob("*.h")}
        if actual_headers != HEADERS or len(list(relocated.rglob("*.h"))) != len(HEADERS):
            raise RuntimeError("client package has incomplete or privileged headers")
        archives = list(relocated.rglob("*.a"))
        if {p.name for p in archives} != LIBRARIES or len(archives) != len(LIBRARIES):
            raise RuntimeError("client archive closure differs")
        # Only the four canonical archives, eight public headers, config and
        # explicit package metadata/docs may be in this developer component.
        for path in relocated.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("SDK contains a symlink")
            if path.is_file() and path.suffix not in {".a", ".h", ".cmake"} and path.name not in {
                    "strategy-client-build-info.txt", "CLIENT_PACKAGE.md", "STRATEGY-GATEWAY.md",
                    "strategy_gateway.py", "hepta-strategy-gateway", "hepta-strategy-native"}:
                raise RuntimeError("unexpected SDK asset: " + str(path.relative_to(relocated)))
        configs = list(relocated.rglob("HeptaStrategyClientConfig.cmake"))
        if len(configs) != 1:
            raise RuntimeError("missing client CMake package")
        for path in relocated.rglob("*.cmake"):
            text = path.read_text(encoding="utf-8")
            if any(value in text for value in (str(source), str(build), str(prefix))):
                raise RuntimeError("nonrelocatable export: " + path.name)
        infos = list(relocated.rglob("strategy-client-build-info.txt"))
        if len(infos) != 1:
            raise RuntimeError("missing client source metadata")
        info = dict(line.split("=", 1) for line in infos[0].read_text().splitlines())
        if info.get("execution_authority") != "none" or info.get("broker_transport") != "none":
            raise RuntimeError("package scope is not client-only")
        if info.get("release_label") != (source / "VERSION").read_text().strip():
            raise RuntimeError("independent or stale release label")
        if info.get("source_tree_state") not in {"clean", "dirty-or-unverifiable", "unavailable"}:
            raise RuntimeError("invalid source provenance state")
        # Compile the existing proposal/wire/error/lifetime test unchanged,
        # copying only its test inputs, not any production source or header.
        consumer = root / "external consumer"
        consumer.mkdir()
        shutil.copyfile(source / "tests/research/native_client_tests.cpp", consumer / "main.cpp")
        shutil.copyfile(source / "tests/research/test_support.h", consumer / "test_support.h")
        (consumer / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        consumer_build = root / "consumer build"
        common = [f"-DHeptaStrategyClient_DIR={configs[0].parent}",
                  "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
                  f"-DCMAKE_BUILD_TYPE={config}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
        for name in ("CMAKE_CXX_COMPILER", "CMAKE_CXX_FLAGS", f"CMAKE_CXX_FLAGS_{config.upper()}",
                     "CMAKE_EXE_LINKER_FLAGS", f"CMAKE_EXE_LINKER_FLAGS_{config.upper()}"):
            if name in cache:
                common.append(f"-D{name}={cache[name]}")
        run([args.cmake, "-S", str(consumer), "-B", str(consumer_build), *common])
        run([args.cmake, "--build", str(consumer_build), "--config", config, "--parallel", "2"])
        executable = consumer_build / "consumer"
        if not executable.exists():
            executable = consumer_build / config / "consumer"
        if "PASS" not in run([str(executable)]):
            raise RuntimeError("installed client behavior did not execute")
        commands = json.loads((consumer_build / "compile_commands.json").read_text())
        if any(value in json.dumps(commands) for value in (str(source), str(build), str(prefix))):
            raise RuntimeError("consumer used original source, build or staging includes")
        # Consume only relocated installed production code, retaining the full
        # original C++ consumer above and the same real service test fixture.
        modules = list(relocated.rglob("strategy_gateway.py"))
        binaries = list(relocated.rglob("hepta-strategy-native"))
        launchers = list(relocated.rglob("hepta-strategy-gateway"))
        if len(modules) != 1 or len(binaries) != 1 or len(launchers) != 1:
            raise RuntimeError("missing/ambiguous application client package")
        if "Application-key" not in run([sys.executable, "-I", "-B", str(launchers[0]), "--help"]):
            raise RuntimeError("relocated application launcher did not execute")
        policy = run([sys.executable, "-I", "-B", str(source / "tests/research/strategy_gateway_behavior.py"),
                      "--adapter", str(modules[0]), "--native", str(binaries[0])])
        if "APPLICATION_POLICY_PASS" not in policy:
            raise RuntimeError("installed application policy cases did not execute")
        execution = build / "research/hepta_research_native_execution_tests"
        if not execution.is_file():
            execution = build / "research" / config / "hepta_research_native_execution_tests"
        observed = run([str(execution), "--application-only", sys.executable,
                        str(source / "tests/research/application_execution_driver.py"),
                        str(modules[0]), str(binaries[0])])
        if "APPLICATION_EXECUTION_PASS" not in observed:
            raise RuntimeError("installed application/real service recovery did not execute")
        # Inspect actual defined symbols. The wire/forwarding archives cannot
        # contain a Gateway, execution runtime, journal or vendor transport.
        nm = cache.get("CMAKE_NM") or shutil.which("nm")
        if not nm:
            raise RuntimeError("nm is required for client archive boundary acceptance")
        symbols = run([nm, "-C", "--defined-only", *map(str, archives), str(binaries[0])])
        for forbidden in ("TradingToolHost::", "TradingToolRegistry::", "ExecutionCoordinator::",
                          "OmsJournal::", "ExecutionServiceRuntimeComposition::", "CThostFtdc"):
            if forbidden in symbols:
                raise RuntimeError("privileged symbol in client package: " + forbidden)
        for name, request, expected in (
            ("execution", "find_package(HeptaStrategyClient CONFIG REQUIRED COMPONENTS Execution)", "Execution"),
            ("wrong-version", "find_package(HeptaStrategyClient 999.0.0 EXACT CONFIG REQUIRED)", "999.0.0"),
        ):
            negative = root / name
            negative.mkdir()
            (negative / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\nproject(Negative LANGUAGES CXX)\n" + request + "\n")
            output = run([args.cmake, "-S", str(negative), "-B", str(root / (name + " build")), *common], success=False)
            if expected not in output:
                raise RuntimeError("negative configure failed for an unrelated reason: " + output)
        # Merely including the client header cannot declare privileged objects.
        # An installed consumer must not accidentally regain a host-only API.
        for forbidden in ("TradingToolHost", "ExecutionAuthority", "TradingToolRegistry"):
            (consumer / "main.cpp").write_text(
                "#include <hepta/research/native_strategy_client.h>\n" +
                "int main() { " + forbidden + "* authority = nullptr; (void)authority; }\n")
            output = run([args.cmake, "--build", str(consumer_build), "--config", config], success=False)
            if forbidden not in output:
                raise RuntimeError("negative compile failed for an unrelated reason: " + output)
        print("CLIENT_SDK_INSTALL_ACCEPTANCE_PASS: relocation, original behavior, pure headers, link closure, exclusions")


if __name__ == "__main__":
    main()
