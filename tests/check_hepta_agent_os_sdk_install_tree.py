#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
import tempfile


COMPONENT = "hepta-agent-os-sdk"
SDK_EXPORT_SUBDIR = PurePosixPath("cmake/HeptaTraderAgentOsSdk")
SDK_ARCHIVE_NAME = "libhepta_native_tool_client.a"
SDK_CONFIG_NAMES = (
    "HeptaTraderAgentOsSdkConfig.cmake",
    "HeptaTraderAgentOsSdkConfigVersion.cmake",
    "HeptaTraderAgentOsSdkTargets.cmake",
)
SDK_HEADER_FILES = {
    "usr/include/heptatrader/client/native_tool_client.h",
    "usr/include/heptatrader/client/native_tool_discovery_contract.h",
    "usr/include/heptatrader/tool_host/typed_tool_protocol.h",
    "usr/include/heptatrader/tool_host/trading_tool_host.h",
    "usr/include/heptatrader/tool_host/trading_tool_session_catalog.h",
    "usr/include/heptatrader/tools/trading_tool_registry.h",
    "usr/include/heptatrader/execution/execution_authority.h",
    "usr/include/heptatrader/execution/trading_contract.h",
    "usr/include/heptatrader/agent/decision_lease_manager.h",
}
ALLOWED_INSTRUMENTATION_FLAGS = {
    ("", ""),
    (
        "-O1 -g -fno-omit-frame-pointer -fsanitize=address",
        "-O1 -g -fno-omit-frame-pointer -fsanitize=address",
    ),
    (
        "-O1 -g -fno-omit-frame-pointer -fsanitize=undefined",
        "-O1 -g -fno-omit-frame-pointer -fsanitize=undefined",
    ),
    (
        "-O1 -g -fno-omit-frame-pointer -fsanitize=thread",
        "-O1 -g -fno-omit-frame-pointer -fsanitize=thread",
    ),
    ("-O0 -g --coverage", "--coverage"),
}


def parent_directories(paths: set[str]) -> set[str]:
    result: set[str] = set()
    for name in paths:
        parent = Path(name).parent
        while parent != Path("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _sdk_libdir(build_root: Path) -> PurePosixPath:
    """Resolve the install libdir recorded by the configured CMake tree.

    GNUInstallDirs selects a multi-arch path (for example,
    ``lib/x86_64-linux-gnu``) on Debian/Ubuntu.  The SDK checker used to
    assume ``usr/lib`` and consequently inspected the wrong tree.  Keep the
    value constrained to a normalized relative POSIX path before using it to
    form paths below; a malicious cache must never escape the staged root.
    """

    raw = cache_value(build_root, "CMAKE_INSTALL_LIBDIR", expected_type="PATH")
    if (
            not raw or "\\" in raw or "\x00" in raw or
            raw.startswith("/") or ":" in raw):
        raise AssertionError("CMAKE_INSTALL_LIBDIR must be a relative POSIX path")
    path = PurePosixPath(raw)
    if (
            path == PurePosixPath(".") or
            any(part in {"", ".", ".."} for part in path.parts) or
            path.as_posix() != raw):
        raise AssertionError(
            "CMAKE_INSTALL_LIBDIR must be normalized and traversal-free")
    return path


def _sdk_relative_files(libdir: PurePosixPath) -> set[str]:
    lib_root = PurePosixPath("usr") / libdir
    export_root = lib_root / SDK_EXPORT_SUBDIR
    files = {(lib_root / SDK_ARCHIVE_NAME).as_posix()}
    files.update(
        (export_root / name).as_posix() for name in SDK_CONFIG_NAMES)
    files.update(SDK_HEADER_FILES)
    return files


def verify_install(
        root: Path, source_root: Path, build_root: Path,
        libdir: PurePosixPath) -> None:
    lib_root = root / "usr" / Path(*libdir.parts)
    export_root = lib_root / Path(*SDK_EXPORT_SUBDIR.parts)
    release_targets = list(export_root.glob("HeptaTraderAgentOsSdkTargets-*.cmake"))
    if len(release_targets) != 1:
        raise AssertionError(
            "SDK export must contain exactly one configuration target file")
    expected_files = _sdk_relative_files(libdir) | {
        release_targets[0].relative_to(root).as_posix()}
    expected_entries = expected_files | parent_directories(expected_files)
    actual_paths = list(root.rglob("*"))
    actual_entries = {path.relative_to(root).as_posix() for path in actual_paths}
    if actual_entries != expected_entries:
        raise AssertionError(
            f"SDK install allowlist mismatch missing="
            f"{sorted(expected_entries - actual_entries)} unexpected="
            f"{sorted(actual_entries - expected_entries)}")

    forbidden = (str(source_root), str(build_root))
    for path in actual_paths:
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AssertionError(f"SDK install tree contains symlink {relative}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o022:
            raise AssertionError(
                f"SDK install entry is writable by group/world: {relative}")
        if relative in expected_files:
            if not stat.S_ISREG(metadata.st_mode) or mode != 0o644:
                raise AssertionError(
                    f"invalid SDK file {relative} mode={mode:04o}")
            if path.suffix == ".cmake":
                text = path.read_text(encoding="utf-8", errors="strict")
                if any(value in text for value in forbidden):
                    raise AssertionError(
                        f"installed SDK metadata leaks build/source path: {relative}")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise AssertionError(f"unexpected SDK non-directory {relative}")

    archive = root / "usr" / Path(*libdir.parts) / SDK_ARCHIVE_NAME
    symbols = subprocess.run(
        ["nm", "-u", "-C", str(archive)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True).stdout
    if "TradingToolRegistry::" in symbols:
        raise AssertionError(
            "native client archive retains an authority-core symbol dependency")


def cache_value(
        build_root: Path, name: str, *, expected_type: str | None = None) -> str:
    cache = build_root / "CMakeCache.txt"
    metadata = cache.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_size > 16 * 1024 * 1024):
        raise AssertionError("SDK parent CMakeCache is unsafe")
    values = []
    for line in cache.read_text(
            encoding="utf-8", errors="strict").splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or ":" not in key:
            continue
        cache_name, cache_type = key.split(":", 1)
        if cache_name != name or (
                expected_type is not None and cache_type != expected_type):
            continue
        values.append(value)
    if len(values) != 1 or "\0" in values[0]:
        suffix = f" of type {expected_type}" if expected_type else ""
        raise AssertionError(f"SDK parent CMakeCache lacks {name}{suffix}")
    return values[0]


def instrumentation_profile(
        build_root: Path) -> tuple[str, str, str]:
    flags = (
        cache_value(build_root, "CMAKE_CXX_FLAGS", expected_type="STRING"),
        cache_value(
            build_root, "CMAKE_EXE_LINKER_FLAGS", expected_type="STRING"),
    )
    if flags not in ALLOWED_INSTRUMENTATION_FLAGS:
        raise AssertionError("SDK parent instrumentation flags are unsupported")
    build_type = cache_value(
        build_root, "CMAKE_BUILD_TYPE", expected_type="STRING")
    expected_type = "Release" if flags == ("", "") else "Debug"
    if build_type != expected_type:
        raise AssertionError(
            "SDK parent build type does not match instrumentation")
    return flags[0], flags[1], build_type


def external_consumer(
        root: Path, source_root: Path, build_root: Path,
        cxx_flags: str, linker_flags: str,
        build_type: str, libdir: PurePosixPath) -> None:
    with tempfile.TemporaryDirectory(
            prefix="hepta-native-sdk-consumer-") as directory:
        workspace = Path(directory)
        source = workspace / "source"
        build = workspace / "build"
        source.mkdir(mode=0o700)
        (source / "CMakeLists.txt").write_text(
            """cmake_minimum_required(VERSION 3.8)
project(HeptaNativeSdkConsumer LANGUAGES CXX)
find_package(HeptaTraderAgentOsSdk 0.1 CONFIG REQUIRED NO_DEFAULT_PATH
  PATHS "${HEPTA_SDK_PREFIX}/${HEPTA_SDK_LIBDIR}/cmake/HeptaTraderAgentOsSdk")
add_executable(hepta_sdk_consumer main.cpp)
target_link_libraries(hepta_sdk_consumer PRIVATE HeptaTrader::NativeToolClient)
""", encoding="utf-8")
        (source / "main.cpp").write_text(
            """#include <heptatrader/client/native_tool_client.h>

#include <string>

int main()
{
    NativeToolClientConfig config;
    config.sessionToken = "installed-sdk-token";
    NativeToolClient client(config);
    TradingToolHostRequest request;
    request.toolCallId = "installed-sdk-link-and-run";
    request.call.name = "system.tools.list";
    NativeToolClientResult result;
    std::string reason;
    if (client.Call(request, result, reason)) return 10;
    if (reason != "INVALID_SOCKET_PATH") return 11;

    TypedToolResultEnvelope envelope;
    if (!TypedToolProtocol::DecodeResultEnvelope(
            "{\\\"status\\\":\\\"ok\\\",\\\"tool\\\":\\\"system.tools.list\\\","
            "\\\"reason_code\\\":\\\"\\\",\\\"detail\\\":\\\"\\\","
            "\\\"order_id\\\":-1,\\\"payload\\\":{\\\"tools\\\":[]}}",
            envelope, reason)) return 12;
    if (envelope.status != "ok" ||
        envelope.toolName != "system.tools.list" ||
        envelope.orderId != -1 ||
        envelope.payloadJson != "{\\\"tools\\\":[]}") return 13;
    return 0;
}
""", encoding="utf-8")
        configure = subprocess.run([
            "cmake", "-S", str(source), "-B", str(build),
            f"-DCMAKE_BUILD_TYPE={build_type}",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            f"-DCMAKE_CXX_FLAGS={cxx_flags}",
            f"-DCMAKE_EXE_LINKER_FLAGS={linker_flags}",
            f"-DHEPTA_SDK_PREFIX={root / 'usr'}",
            f"-DHEPTA_SDK_LIBDIR={libdir.as_posix()}",
        ], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        if configure.returncode != 0:
            raise AssertionError(
                "installed SDK external configure failed:\n" + configure.stdout)
        compiled = subprocess.run(
            ["cmake", "--build", str(build), "--verbose"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        if compiled.returncode != 0:
            raise AssertionError(
                "installed SDK external link failed:\n" + compiled.stdout)
        executable = build / "hepta_sdk_consumer"
        subprocess.run([str(executable)], check=True, timeout=10)

        compile_commands = json.loads(
            (build / "compile_commands.json").read_text(
                encoding="utf-8", errors="strict"))
        transcript = configure.stdout + compiled.stdout + json.dumps(
            compile_commands, sort_keys=True)
        for forbidden in (str(source_root), str(build_root)):
            if forbidden in transcript:
                raise AssertionError(
                    "external SDK consumer leaked repository build/source path")
        installed_include = str(root / "usr/include")
        installed_archive = str(
            root / "usr" / Path(*libdir.parts) / SDK_ARCHIVE_NAME)
        if installed_include not in transcript:
            raise AssertionError(
                "external SDK consumer did not compile against installed headers")
        if installed_archive not in compiled.stdout:
            raise AssertionError(
                "external SDK consumer did not link the installed archive")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--cmake", default="cmake")
    args = parser.parse_args()
    build = args.build_dir.resolve(strict=True)
    source = args.source_dir.resolve(strict=True)
    libdir = _sdk_libdir(build)
    with tempfile.TemporaryDirectory(
            prefix="hepta-agent-os-sdk-install-") as directory:
        root = Path(directory) / "root"
        environment = os.environ.copy()
        environment["DESTDIR"] = str(root)
        previous_umask = os.umask(0o022)
        try:
            installed = subprocess.run([
                args.cmake, "--install", str(build), "--prefix", "/usr",
                "--component", COMPONENT,
            ], check=False, env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True)
        finally:
            os.umask(previous_umask)
        if installed.returncode != 0:
            raise AssertionError(
                "SDK component install failed:\n" + installed.stdout)
        verify_install(root, source, build, libdir)
        cxx_flags, linker_flags, build_type = instrumentation_profile(
            build)
        external_consumer(
            root, source, build, cxx_flags, linker_flags, build_type, libdir)
    print("hepta_agent_os_sdk_install_tree: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
