#!/usr/bin/env python3
"""One-shot exact patch for result-envelope aggregate preflight.

This file and its temporary workflow delete themselves before the tested
repair is committed.
"""

from pathlib import Path
import re


WIRE = Path("HeptaTrade/tools/trading_tool_wire_contract.h")
TEST = Path("tests/unix_tool_server_tests.cpp")
WORKFLOW = Path(".github/workflows/gap-closure-patch.yml")
SELF = Path(__file__)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new)


def patch_wire_contract() -> None:
    text = WIRE.read_text(encoding="utf-8")
    if "#include <limits>" not in text:
        text = replace_once(
            text,
            "#include <locale>\n",
            "#include <locale>\n#include <limits>\n",
            "include layout",
        )

    old_size = (
        "    static std::size_t EncodedResultEnvelopeSize("
        "const TradingToolResult& result)\n"
        "    {\n"
        "        return EncodeResultEnvelopeBody(result).size();\n"
        "    }\n"
    )
    new_size = (
        "    static std::size_t EncodedResultEnvelopeSize("
        "const TradingToolResult& result)\n"
        "    {\n"
        "        // Measure the caller's raw candidate, not the fail-closed "
        "serialized\n"
        "        // fallback. EncodeResultEnvelopeBody deliberately replaces "
        "an\n"
        "        // invalid payload with null; using that sanitized body here "
        "would\n"
        "        // undercount an oversized compound response and defer "
        "rejection until\n"
        "        // the socket layer, losing the compound tool's canonical "
        "reason code.\n"
        "        const std::string prefix = EncodeResultEnvelopePrefix(result);\n"
        "        const std::size_t payloadSize =\n"
        "            result.payloadJson.empty() ? 4u : "
        "result.payloadJson.size();\n"
        "        const std::size_t maximum =\n"
        "            std::numeric_limits<std::size_t>::max();\n"
        "        if (prefix.size() > maximum - 1u ||\n"
        "            payloadSize > maximum - prefix.size() - 1u)\n"
        "            return maximum;\n"
        "        return prefix.size() + payloadSize + 1u;\n"
        "    }\n"
    )
    text = replace_once(text, old_size, new_size, "size preflight")

    pattern = re.compile(
        r"    static std::string EncodeResultEnvelopeBody\("
        r"const TradingToolResult& result\)\n"
        r"    \{\n.*?\n    \}\n\n"
        r"    static std::string EscapeJson",
        re.DOTALL,
    )
    replacement = (
        "    static std::string EncodeResultEnvelopePrefix(\n"
        "        const TradingToolResult& result)\n"
        "    {\n"
        "        std::ostringstream out;\n"
        "        // Result envelopes are serialized onto the native/MCP "
        "boundary.\n"
        "        // Keep numeric formatting independent of the process-global "
        "locale.\n"
        "        out.imbue(std::locale::classic());\n"
        "        out << \"{\\\"status\\\":\\\"\" << "
        "StatusName(result.status)\n"
        "            << \"\\\",\\\"tool\\\":\\\"\" << "
        "EscapeJson(result.toolName)\n"
        "            << \"\\\",\\\"reason_code\\\":\\\"\" << "
        "EscapeJson(result.reasonCode)\n"
        "            << \"\\\",\\\"detail\\\":\\\"\" << "
        "EscapeJson(result.detail)\n"
        "            << \"\\\",\\\"order_id\\\":\" << result.orderId\n"
        "            << \",\\\"payload\\\":\";\n"
        "        return out.str();\n"
        "    }\n\n"
        "    static std::string EncodeResultEnvelopeBody(\n"
        "        const TradingToolResult& result)\n"
        "    {\n"
        "        std::string encoded = EncodeResultEnvelopePrefix(result);\n"
        "        // Validate raw payload JSON before appending. Invalid payloads\n"
        "        // fail closed as null; the Unix server rejects the original\n"
        "        // result with a canonical INVALID/UNCERTAIN envelope.\n"
        "        const bool payloadSafe = !result.payloadJson.empty() &&\n"
        "            IsSafePayloadJson(result.payloadJson);\n"
        "        encoded += payloadSafe ? result.payloadJson : \"null\";\n"
        "        encoded.push_back('}');\n"
        "        return encoded;\n"
        "    }\n\n"
        "    static std::string EscapeJson"
    )
    text, count = pattern.subn(replacement, text)
    if count != 1:
        raise SystemExit(f"envelope body: expected one match, found {count}")
    WIRE.write_text(text, encoding="utf-8")


def patch_watch_fixture() -> None:
    text = TEST.read_text(encoding="utf-8")
    old = (
        "std::string(\n"
        "            TradingToolWireLimits::MaximumResultEnvelopeBytes(), 'x')"
    )
    new = (
        "std::string(\n"
        "            TradingToolWireLimits::MaximumResultEnvelopeBytes() - "
        "1024u, 'x')"
    )
    TEST.write_text(
        replace_once(text, old, new, "WATCH aggregate fixture"),
        encoding="utf-8",
    )


def main() -> None:
    patch_wire_contract()
    patch_watch_fixture()
    WORKFLOW.unlink()
    SELF.unlink()


if __name__ == "__main__":
    main()
