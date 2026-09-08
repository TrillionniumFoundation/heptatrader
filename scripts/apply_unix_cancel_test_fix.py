#!/usr/bin/env python3
"""Make cancellation lifecycle test synchronization deterministic."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests/unix_tool_server_tests.cpp"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor, found {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)


replace_once(
    '''    ExecutionCoordinator execution(journal, executionCallbacks);\n    TradingToolReadCallbacks reads;\n    reads.marketGetQuote = [](const TradingToolSession& session, const TradingToolCall&,\n                              std::string& payload, std::string&) {\n        if (session.executionContext.toolCallId.find("block-") == 0) usleep(250000);\n        payload = "{}";\n        return true;\n    };\n''',
    '''    ExecutionCoordinator execution(journal, executionCallbacks);\n    std::atomic<bool> releaseCancelBlocker(false);\n    TradingToolReadCallbacks reads;\n    reads.marketGetQuote = [&releaseCancelBlocker](\n                              const TradingToolSession& session,\n                              const TradingToolCall&, std::string& payload,\n                              std::string&) {\n        const std::string& callId = session.executionContext.toolCallId;\n        if (callId == "block-cancel")\n        {\n            while (!releaseCancelBlocker.load(std::memory_order_acquire))\n                usleep(1000);\n        }\n        else if (callId.find("block-") == 0) usleep(250000);\n        payload = "{}";\n        return true;\n    };\n''',
)

replace_once(
    '''    std::string targetResponse;\n    std::thread target([&]() { targetResponse = call(quote("cancel-target")); });\n    while (server.GetHealth().pendingConnections == 0) usleep(1000);\n''',
    '''    std::string targetResponse;\n    std::thread target([&]() { targetResponse = call(quote("cancel-target")); });\n    // pendingConnections increments at accept time and therefore does not prove\n    // the target is cancel-addressable. Hold the active request and wait until\n    // the owner queue is visibly ready before racing the out-of-band cancel.\n    while (server.GetHealth().readyOwners == 0) usleep(1000);\n''',
)

replace_once(
    '''    const std::string cancelResponse = call(cancel);\n    target.join();\n    secondBlocker.join();\n''',
    '''    const std::string cancelResponse = call(cancel);\n    releaseCancelBlocker.store(true, std::memory_order_release);\n    target.join();\n    secondBlocker.join();\n''',
)

PATH.write_text(text, encoding="utf-8")
print("deterministic cancel-queue synchronization materialized")
