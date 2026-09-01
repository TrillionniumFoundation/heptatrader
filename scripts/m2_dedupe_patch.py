#!/usr/bin/env python3
"""One-shot M2 patch: compile every production source through one module target.

The patch removes direct production-source compilation from tests and runtime
executables, introduces explicit OMS/state/intent/protocol/venue libraries,
updates module ownership, regenerates deterministic views, and deletes itself
plus its temporary workflow before the tested commit is created.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TRADE_CMAKE = ROOT / "HeptaTrade/CMakeLists.txt"
TEST_CMAKE = ROOT / "tests/CMakeLists.txt"
OWNERSHIP = ROOT / "docs/modules/source-ownership-registry-v1.json"
CHECKER = ROOT / "scripts/check_cmake_module_graph.py"
WORKFLOW = ROOT / ".github/workflows/gap-closure-patch.yml"
SELF = Path(__file__)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new)


def rewrite_add_sources(
    text: str,
    target: str,
    *,
    remove_exact: set[str] | None = None,
    remove_prefixes: tuple[str, ...] = (),
    append: tuple[str, ...] = (),
) -> str:
    pattern = re.compile(
        rf"(add_(?:library|executable)\({re.escape(target)}(?:\s+STATIC)?\n)"
        rf"(.*?)(\n\))",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise SystemExit(f"target source block not found: {target}")
    remove_exact = remove_exact or set()
    lines = match.group(2).splitlines()
    kept: list[str] = []
    for line in lines:
        token = line.strip()
        if token in remove_exact or any(token.startswith(prefix) for prefix in remove_prefixes):
            continue
        kept.append(line)
    existing = {line.strip() for line in kept}
    for source in append:
        if source not in existing:
            kept.append(f"    {source}")
    replacement = match.group(1) + "\n".join(kept) + match.group(3)
    return text[: match.start()] + replacement + text[match.end() :]


def add_link_libraries(text: str, target: str, libraries: tuple[str, ...]) -> str:
    marker = f"target_link_libraries({target}"
    start = text.find(marker)
    missing = [library for library in libraries if library not in text]
    # The broad text check above is only an optimization; calculate against the
    # actual command below before changing it.
    if start >= 0:
        opening = text.find("(", start)
        depth = 0
        end = -1
        for index in range(opening, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end < 0:
            raise SystemExit(f"unbalanced target_link_libraries for {target}")
        command = text[start : end + 1]
        missing = [library for library in libraries if re.search(rf"\b{re.escape(library)}\b", command) is None]
        if not missing:
            return text
        insertion = "".join(f"\n    {library}" for library in missing)
        command = command[:-1] + insertion + ")"
        return text[:start] + command + text[end + 1 :]

    add_start = text.find(f"add_executable({target}")
    if add_start < 0:
        raise SystemExit(f"cannot add link command; target missing: {target}")
    opening = text.find("(", add_start)
    depth = 0
    add_end = -1
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                add_end = index
                break
    if add_end < 0:
        raise SystemExit(f"unbalanced add_executable for {target}")
    command = "\ntarget_link_libraries(" + target + " PRIVATE"
    command += "".join(f"\n    {library}" for library in libraries)
    command += ")"
    return text[: add_end + 1] + command + text[add_end + 1 :]


def patch_trade_cmake() -> None:
    text = TRADE_CMAKE.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "add_library(hepta_agent_execution_support STATIC\n"
        "    agent/decision_lease_manager.cpp\n"
        "    events/execution_event_hub.cpp)",
        "add_library(hepta_agent_execution_support STATIC\n"
        "    agent/decision_lease_manager.cpp\n"
        "    events/execution_event_hub.cpp\n"
        "    events/owner_scoped_health_publisher.cpp)",
        "agent support ownership",
    )

    insertion_marker = (
        "add_library(hepta_risk_core STATIC\n"
        "    risk/deterministic_risk_policy.cpp)\n"
        "hepta_runtime_target(hepta_risk_core)\n"
        "target_link_libraries(hepta_risk_core PUBLIC hepta_observability_core)\n"
    )
    new_targets = insertion_marker + """

add_library(hepta_oms_core STATIC
    oms_journal.cpp
    oms_recover.cpp)
hepta_runtime_target(hepta_oms_core)
target_link_libraries(hepta_oms_core PUBLIC
    hepta_observability_core
    Threads::Threads)

add_library(hepta_state_core STATIC
    state/authoritative_trading_snapshot_store.cpp
    state/snapshot_refresh_coordinator.cpp
    state/ib_contract_identity.cpp
    state/ib_authoritative_quote_subscription_set.cpp)
hepta_runtime_target(hepta_state_core)
target_link_libraries(hepta_state_core PUBLIC Threads::Threads)

add_library(hepta_intent_core STATIC
    intent/bounded_json.cpp
    intent/target_position_intent.cpp
    intent/authoritative_decision_snapshot.cpp)
hepta_runtime_target(hepta_intent_core)
target_link_libraries(hepta_intent_core PUBLIC OpenSSL::Crypto)

add_library(hepta_tool_protocol STATIC
    tool_host/typed_tool_framing.cpp
    tool_host/typed_tool_protocol.cpp
    tool_host/typed_tool_result_codec.cpp)
hepta_runtime_target(hepta_tool_protocol)

add_library(hepta_simulator_venue STATIC
    simulator/deterministic_execution_venue.cpp)
hepta_runtime_target(hepta_simulator_venue)

add_library(hepta_venue_ctp STATIC
    adapter_ctp/ctp_gateway_adapter.cpp)
hepta_runtime_target(hepta_venue_ctp)

add_library(hepta_venue_xt STATIC
    adapter_xt/xt_gateway_adapter.cpp)
hepta_runtime_target(hepta_venue_xt)

add_library(hepta_ib_adapter_core STATIC
    adapter_ib/ib_api_wrapper.cpp
    adapter_ib/ib_decimal_compat.cpp
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    adapter_ib/ib_order_lifecycle.cpp
    adapter_ib/ib_venue_correlation.cpp)
hepta_runtime_target(hepta_ib_adapter_core)
target_link_libraries(hepta_ib_adapter_core PUBLIC
    hepta_risk_core
    hepta_observability_core
    Threads::Threads)
"""
    text = replace_once(text, insertion_marker, new_targets.rstrip("\n"), "module target insertion")

    text = rewrite_add_sources(
        text,
        "hepta_execution_core",
        remove_exact={"oms_journal.cpp"},
        append=("execution/ib_paper_kill_switch.cpp",),
    )
    text = replace_once(
        text,
        "target_link_libraries(hepta_execution_core PUBLIC\n"
        "    hepta_execution_server\n"
        "    hepta_execution_client\n"
        "    hepta_agent_execution_support\n"
        "    hepta_risk_core\n"
        "    Threads::Threads\n"
        "    OpenSSL::Crypto)",
        "target_link_libraries(hepta_execution_core PUBLIC\n"
        "    hepta_execution_server\n"
        "    hepta_execution_client\n"
        "    hepta_agent_execution_support\n"
        "    hepta_risk_core\n"
        "    hepta_oms_core\n"
        "    Threads::Threads\n"
        "    OpenSSL::Crypto)",
        "execution core links",
    )

    text = rewrite_add_sources(
        text,
        "hepta_trading_tool_core",
        remove_prefixes=("state/", "intent/"),
    )
    text = replace_once(
        text,
        "target_link_libraries(hepta_trading_tool_core PUBLIC OpenSSL::Crypto)",
        "target_link_libraries(hepta_trading_tool_core PUBLIC\n"
        "    hepta_intent_core\n"
        "    hepta_state_core\n"
        "    OpenSSL::Crypto)",
        "tool registry links",
    )

    text = rewrite_add_sources(
        text,
        "hepta_agent_os_core",
        remove_prefixes=("tool_host/typed_tool_",),
    )
    text = replace_once(
        text,
        "    hepta_observability_core\n"
        "    hepta_trading_tool_core\n",
        "    hepta_observability_core\n"
        "    hepta_tool_protocol\n"
        "    hepta_trading_tool_core\n",
        "gateway protocol link",
    )

    text = rewrite_add_sources(
        text,
        "hepta_native_tool_client",
        remove_prefixes=("tool_host/typed_tool_",),
    )
    text = replace_once(
        text,
        "hepta_runtime_target(hepta_native_tool_client)\n\nadd_executable(heptactl",
        "hepta_runtime_target(hepta_native_tool_client)\n"
        "target_link_libraries(hepta_native_tool_client PUBLIC\n"
        "    hepta_tool_protocol)\n\nadd_executable(heptactl",
        "native protocol link",
    )

    text = rewrite_add_sources(
        text,
        "hepta_executiond",
        remove_exact={"simulator/deterministic_execution_venue.cpp"},
    )
    text = replace_once(
        text,
        "target_link_libraries(hepta_executiond PRIVATE hepta_execution_core)",
        "target_link_libraries(hepta_executiond PRIVATE\n"
        "    hepta_execution_core\n"
        "    hepta_simulator_venue)",
        "simulator target link",
    )

    text = rewrite_add_sources(
        text,
        "hepta_ib_executiond",
        remove_exact={
            "execution/ib_paper_kill_switch.cpp",
            "${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}",
            "state/authoritative_trading_snapshot_store.cpp",
            "state/ib_authoritative_quote_subscription_set.cpp",
            "state/ib_contract_identity.cpp",
        },
        remove_prefixes=("adapter_ib/",),
    )
    text = replace_once(
        text,
        "    target_compile_definitions(hepta_ibapi_client PUBLIC HEPTA_ENABLE_IBAPI=1)\n\n"
        "    add_executable(hepta_ib_executiond",
        "    target_compile_definitions(hepta_ibapi_client PUBLIC HEPTA_ENABLE_IBAPI=1)\n\n"
        "    target_compile_definitions(hepta_ib_adapter_core PUBLIC HEPTA_ENABLE_IBAPI=1)\n"
        "    target_include_directories(hepta_ib_adapter_core PRIVATE \"${IBAPI_ROOT}\")\n"
        "    target_link_libraries(hepta_ib_adapter_core PUBLIC hepta_ibapi_client)\n\n"
        "    add_executable(hepta_ib_executiond",
        "IB adapter SDK binding",
    )
    text = replace_once(
        text,
        "    target_link_libraries(hepta_ib_executiond PRIVATE\n"
        "        hepta_execution_core\n"
        "        hepta_risk_core\n"
        "        hepta_ibapi_client)",
        "    target_link_libraries(hepta_ib_executiond PRIVATE\n"
        "        hepta_execution_core\n"
        "        hepta_state_core\n"
        "        hepta_ib_adapter_core\n"
        "        hepta_ibapi_client)",
        "IB daemon module links",
    )

    TRADE_CMAKE.write_text(text, encoding="utf-8")


def remove_direct_production_sources(text: str) -> str:
    cursor = 0
    output: list[str] = []
    pattern = re.compile(r"add_executable\((hepta_[A-Za-z0-9_.+-]+)\b")
    while True:
        match = pattern.search(text, cursor)
        if match is None:
            output.append(text[cursor:])
            break
        output.append(text[cursor : match.start()])
        opening = text.find("(", match.start())
        depth = 0
        end = -1
        for index in range(opening, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end < 0:
            raise SystemExit(f"unbalanced test add_executable: {match.group(1)}")
        command = text[match.start() : end + 1]
        lines = command[:-1].splitlines()
        filtered = [line for line in lines if "../HeptaTrade/" not in line]
        output.append("\n".join(filtered) + ")")
        cursor = end + 1
    return "".join(output)


def patch_tests() -> None:
    text = TEST_CMAKE.read_text(encoding="utf-8")
    text = remove_direct_production_sources(text)
    additions = {
        "hepta_execution_coordinator_tests": ("hepta_oms_core",),
        "hepta_oms_journal_durability_tests": ("hepta_oms_core",),
        "hepta_execution_event_hub_tests": ("hepta_agent_execution_support",),
        "hepta_agent_simulator_e2e_tests": ("hepta_state_core", "hepta_simulator_venue"),
        "hepta_decision_lease_manager_tests": ("hepta_agent_execution_support",),
        "hepta_authoritative_trading_snapshot_store_tests": ("hepta_state_core",),
        "hepta_snapshot_refresh_coordinator_tests": ("hepta_state_core",),
        "hepta_ib_order_lifecycle_tests": ("hepta_ib_adapter_core",),
        "hepta_ib_gateway_adapter_risk_tests": ("hepta_ib_adapter_core",),
        "hepta_ib_paper_kill_switch_tests": ("hepta_execution_core",),
        "hepta_target_position_intent_tests": ("hepta_intent_core",),
        "hepta_authoritative_decision_snapshot_tests": ("hepta_intent_core",),
        "hepta_unsupported_venue_adapter_tests": ("hepta_venue_ctp", "hepta_venue_xt"),
        "hepta_oms_crash_replay_tests": ("hepta_oms_core",),
    }
    for target, libraries in additions.items():
        text = add_link_libraries(text, target, libraries)
    TEST_CMAKE.write_text(text, encoding="utf-8")


def update_manifest(name: str, *, add_targets: tuple[str, ...] = (), remove_targets: tuple[str, ...] = (), add_dependencies: tuple[str, ...] = ()) -> None:
    path = ROOT / f"docs/modules/manifests/{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = [value for value in data.get("build_targets", []) if value not in remove_targets]
    for value in add_targets:
        if value not in targets:
            targets.append(value)
    data["build_targets"] = targets
    dependencies = list(data.get("allowed_dependencies", []))
    for value in add_dependencies:
        if value not in dependencies:
            dependencies.append(value)
    data["allowed_dependencies"] = dependencies
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def patch_manifests_and_ownership() -> None:
    update_manifest(
        "hepta-execution-runtime",
        add_targets=("hepta_oms_core", "hepta_state_core", "hepta_intent_core"),
        remove_targets=("hepta_trading_tool_core",),
    )
    update_manifest("hepta-gateway-runtime", add_targets=("hepta_trading_tool_core",))
    update_manifest("hepta-protocol-contracts", add_targets=("hepta_tool_protocol",))
    update_manifest("hepta-venue-simulator", add_targets=("hepta_simulator_venue",))
    update_manifest("hepta-venue-ctp", add_targets=("hepta_venue_ctp",))
    update_manifest("hepta-venue-xt", add_targets=("hepta_venue_xt",))
    update_manifest(
        "hepta-venue-ib",
        add_targets=("hepta_ib_adapter_core",),
        add_dependencies=("hepta.risk.policy", "hepta.observability.runtime"),
    )

    data = json.loads(OWNERSHIP.read_text(encoding="utf-8-sig"))
    filtered = []
    for item in data.get("compilation_exceptions", []):
        target = item.get("target")
        source = item.get("source", "")
        if item.get("target_owner") == "hepta.tests":
            continue
        if target in {"hepta_trading_tool_core", "hepta_agent_os_core", "hepta_executiond", "hepta_ib_executiond"}:
            continue
        if target == "hepta_native_tool_client" and "/typed_tool_" in source:
            continue
        filtered.append(item)
    data["compilation_exceptions"] = filtered
    OWNERSHIP.write_text(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    checker = CHECKER.read_text(encoding="utf-8")
    checker = replace_once(
        checker,
        "from hepta_module_boundaries import (\n    SOURCE_OWNERSHIP_REL,",
        "from hepta_module_boundaries import (\n    ACTIVE_LIFECYCLES,\n    SOURCE_OWNERSHIP_REL,",
        "checker active lifecycle import",
    )
    checker = replace_once(
        checker,
        "        if manifest.get(\"lifecycle\") not in {\"current\", \"experimental\"}:\n",
        "        if manifest.get(\"lifecycle\") not in ACTIVE_LIFECYCLES:\n",
        "unsupported fail-closed target ownership",
    )
    CHECKER.write_text(checker, encoding="utf-8")


def main() -> None:
    patch_trade_cmake()
    patch_tests()
    patch_manifests_and_ownership()
    subprocess.run(
        ["python3", str(ROOT / "scripts/generate_documentation_views.py")],
        cwd=ROOT,
        check=True,
    )
    WORKFLOW.unlink()
    SELF.unlink()


if __name__ == "__main__":
    main()
