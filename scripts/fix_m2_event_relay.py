#!/usr/bin/env python3
"""Extend the one-shot M2 patch with a single-owned event-relay target."""

from pathlib import Path

path = Path("scripts/m2_dedupe_patch.py")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        "add_library(hepta_tool_protocol STATIC\n"
        "    tool_host/typed_tool_framing.cpp\n"
        "    tool_host/typed_tool_protocol.cpp\n"
        "    tool_host/typed_tool_result_codec.cpp)\n"
        "hepta_runtime_target(hepta_tool_protocol)\n\n"
        "add_library(hepta_simulator_venue STATIC",
        "add_library(hepta_tool_protocol STATIC\n"
        "    tool_host/typed_tool_framing.cpp\n"
        "    tool_host/typed_tool_protocol.cpp\n"
        "    tool_host/typed_tool_result_codec.cpp)\n"
        "hepta_runtime_target(hepta_tool_protocol)\n\n"
        "add_library(hepta_execution_event_relay_core STATIC\n"
        "    tool_host/execution_event_relay.cpp)\n"
        "hepta_runtime_target(hepta_execution_event_relay_core)\n"
        "target_link_libraries(hepta_execution_event_relay_core PUBLIC\n"
        "    hepta_execution_contract\n"
        "    hepta_agent_execution_support)\n\n"
        "add_library(hepta_simulator_venue STATIC",
        "relay target insertion",
    ),
    (
        "        \"hepta_agent_os_core\",\n"
        "        remove_prefixes=(\"tool_host/typed_tool_\",),\n"
        "    )",
        "        \"hepta_agent_os_core\",\n"
        "        remove_exact={\"tool_host/execution_event_relay.cpp\"},\n"
        "        remove_prefixes=(\"tool_host/typed_tool_\",),\n"
        "    )",
        "gateway relay source extraction",
    ),
    (
        "        \"    hepta_observability_core\\n\"\n"
        "        \"    hepta_tool_protocol\\n\"\n"
        "        \"    hepta_trading_tool_core\\n\",",
        "        \"    hepta_observability_core\\n\"\n"
        "        \"    hepta_tool_protocol\\n\"\n"
        "        \"    hepta_execution_event_relay_core\\n\"\n"
        "        \"    hepta_trading_tool_core\\n\",",
        "gateway relay link",
    ),
    (
        "        \"hepta_execution_event_hub_tests\": (\"hepta_agent_execution_support\",),\n"
        "        \"hepta_agent_simulator_e2e_tests\":",
        "        \"hepta_execution_event_hub_tests\": (\"hepta_agent_execution_support\",),\n"
        "        \"hepta_execution_event_feed_tests\": (\"hepta_execution_event_relay_core\",),\n"
        "        \"hepta_agent_simulator_e2e_tests\":",
        "relay test link",
    ),
    (
        "    update_manifest(\"hepta-gateway-runtime\", add_targets=(\"hepta_trading_tool_core\",))",
        "    update_manifest(\n"
        "        \"hepta-gateway-runtime\",\n"
        "        add_targets=(\"hepta_trading_tool_core\", \"hepta_execution_event_relay_core\"),\n"
        "    )",
        "relay manifest target",
    ),
]

for old, new, label in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new)

path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
