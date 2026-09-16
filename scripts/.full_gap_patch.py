from pathlib import Path
from textwrap import dedent


def require(cond, msg):
    if not cond:
        raise SystemExit(msg)


# 1) OMS generation verification: cumulative indexes are validated as streams.
path = Path("scripts/hepta_oms_lifecycle.py")
text = path.read_text()
require("MAX_CHAIN = 1024\n" in text, "MAX_CHAIN marker missing")
text = text.replace("MAX_CHAIN = 1024\n", "", 1)

insert_marker = "\n\ndef _iter_private_lines(path: Path) -> Iterator[bytes]:\n"
require(insert_marker in text, "index iterator marker missing")
validators = dedent(r'''
def _validate_runtime_index_stream(path: Path, expected_records: int) -> None:
    count = 0
    previous: tuple[str, str, str] | None = None
    for line in _iter_private_lines(path):
        key, _, _ = _runtime_row(line)
        if previous is not None and key <= previous:
            raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")
        previous = key
        count += 1
    if count != expected_records:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")


def _validate_send_index_stream(path: Path, expected_records: int, *, ordered: bool) -> None:
    count = 0
    previous: tuple[str, str, int, int, str, str, str] | None = None
    for line in _iter_private_lines(path):
        count += 1
        if not ordered:
            continue
        if len(line) > v1.MAX_INDEX_LINE:
            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_RECORD_INVALID")
        try:
            fields = line.rstrip(b"\n").decode("ascii").split("\t")
            if len(fields) != 7:
                raise ValueError("field count")
            v1._unhex(fields[0]); v1._unhex(fields[1])
            v1._unhex(fields[3]); v1._unhex(fields[4]); v1._unhex(fields[5])
            key = (fields[0], fields[1], int(fields[2]), int(fields[6]),
                   fields[3], fields[4], fields[5])
        except (UnicodeError, ValueError, OverflowError, v1.GenerationError) as error:
            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_RECORD_INVALID") from error
        if previous is not None and key < previous:
            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")
        previous = key
    if count != expected_records:
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")


''')
text = text.replace(insert_marker, "\n\n" + validators + "def _iter_private_lines(path: Path) -> Iterator[bytes]:\n", 1)

chain_limit = '        if len(chain) >= MAX_CHAIN:\n            raise v1.GenerationError("OMS_GENERATION_CHAIN_LIMIT")\n'
require(chain_limit in text, "generation chain ceiling block missing")
text = text.replace(chain_limit, "", 1)

start = text.find('    runtime_lines = list(_iter_private_lines(root / "runtime-command-index.tsv"))')
end = text.find("\n    if journal is not None:", start)
require(start >= 0 and end > start, "verify_generation cumulative index block missing")
replacement = '''    _validate_runtime_index_stream(
        root / "runtime-command-index.tsv", command_records)
    _validate_send_index_stream(
        root / "send-attempt-index.tsv", send_attempt_records,
        ordered=send_index_order == SEND_INDEX_ORDER)
'''
text = text[:start] + replacement + text[end:]
path.write_text(text)

# 2) Owner/session scope for HPM2 sealed summaries and legacy enumeration.
path = Path("HeptaTrade/oms_generation_store.h")
h = path.read_text()
old = dedent('''
    bool EnumerateMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        std::vector<OmsGenerationMutationRecord>& records,
        std::string& reason) const;

    bool SummarizeMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        OmsGenerationMutationSummary& summary,
        std::string& reason) const;
''').strip("\n")
new = dedent('''
    bool EnumerateMutationRecords(
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& account,
        const std::string& executionDomain,
        std::vector<OmsGenerationMutationRecord>& records,
        std::string& reason) const;

    bool SummarizeMutationRecords(
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& account,
        const std::string& executionDomain,
        OmsGenerationMutationSummary& summary,
        std::string& reason) const;
''').strip("\n")
require(h.count(old) == 1, f"oms_generation_store.h signature block count={h.count(old)}")
path.write_text(h.replace(old, new, 1))

path = Path("HeptaTrade/execution/execution_generation_support.cpp")
c = path.read_text()
old_sig = '''bool OmsGenerationStore::EnumerateMutationRecords(
    const std::string& account,
    const std::string& executionDomain,
'''
new_sig = '''bool OmsGenerationStore::EnumerateMutationRecords(
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& account,
    const std::string& executionDomain,
'''
require(c.count(old_sig) == 1, f"enumerate sig count={c.count(old_sig)}")
c = c.replace(old_sig, new_sig, 1)

enum_start = c.index("bool OmsGenerationStore::EnumerateMutationRecords(")
enum_end = c.index("bool OmsGenerationStore::SummarizeMutationRecords(", enum_start)
enum_block = c[enum_start:enum_end]
require(enum_block.count("        std::string rowAccount, rowDomain;") == 1, "enum row declaration mismatch")
enum_block = enum_block.replace(
    "        std::string rowAccount, rowDomain;",
    "        std::string rowAgent, rowSession, rowAccount, rowDomain;", 1)
old_decode = '''        if (!GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
'''
new_decode = '''        if (!GenerationDecodeHex(fields[0], rowAgent) ||
            !GenerationDecodeHex(fields[1], rowSession) ||
            !GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
'''
require(enum_block.count(old_decode) == 1, "enum decode mismatch")
enum_block = enum_block.replace(old_decode, new_decode, 1)
old_filter = '''        if (fields[12] == "1" && rowAccount == account &&
            rowDomain == executionDomain)
'''
new_filter = '''        if (fields[12] == "1" && rowAgent == agentId &&
            rowSession == sessionId && rowAccount == account &&
            rowDomain == executionDomain)
'''
require(enum_block.count(old_filter) == 1, "enum filter mismatch")
enum_block = enum_block.replace(old_filter, new_filter, 1)
c = c[:enum_start] + enum_block + c[enum_end:]

old_sig = '''bool OmsGenerationStore::SummarizeMutationRecords(
    const std::string& account,
    const std::string& executionDomain,
'''
new_sig = '''bool OmsGenerationStore::SummarizeMutationRecords(
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& account,
    const std::string& executionDomain,
'''
require(c.count(old_sig) == 1, f"summary sig count={c.count(old_sig)}")
c = c.replace(old_sig, new_sig, 1)
sum_start = c.index("bool OmsGenerationStore::SummarizeMutationRecords(")
sum_end = c.index("ExecutionCoordinator::RequestRecordStore::RequestRecordStore", sum_start)
sum_block = c[sum_start:sum_end]
require(sum_block.count("        std::string rowAccount, rowDomain;") == 1, "summary row declaration mismatch")
sum_block = sum_block.replace(
    "        std::string rowAccount, rowDomain;",
    "        std::string rowAgent, rowSession, rowAccount, rowDomain;", 1)
require(sum_block.count(old_decode) == 1, "summary decode mismatch")
sum_block = sum_block.replace(old_decode, new_decode, 1)
require(sum_block.count(old_filter) == 1, "summary filter mismatch")
sum_block = sum_block.replace(old_filter, new_filter, 1)
c = c[:sum_start] + sum_block + c[sum_end:]

old_call = '''    if (!m_generationStore.SummarizeMutationRecords(
            binding.owner.account, binding.owner.executionDomain,
            sealed, reason))
'''
new_call = '''    if (!m_generationStore.SummarizeMutationRecords(
            binding.owner.agentId, binding.owner.sessionId,
            binding.owner.account, binding.owner.executionDomain,
            sealed, reason))
'''
require(c.count(old_call) == 1, "terminal summary call mismatch")
c = c.replace(old_call, new_call, 1)
old_tail = '''        if (!request.durableMutationIntent ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
'''
new_tail = '''        if (!request.durableMutationIntent ||
            request.context.agentId != binding.owner.agentId ||
            request.context.sessionId != binding.owner.sessionId ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
'''
require(c.count(old_tail) == 1, "active tail owner scope mismatch")
c = c.replace(old_tail, new_tail, 1)
path.write_text(c)

# 3) Native generation regression for same-account/domain foreign session.
path = Path("tests/oms_recovery_growth_probe.h")
t = path.read_text()
old = '''        assert(coordinator.RuntimeObservation().orderOwners == 0);
        assert(sends == 1);
    }

    // Exercise the exact v2 stopped-state producer
'''
new = '''        assert(coordinator.RuntimeObservation().orderOwners == 0);
        auto foreignCommand = MakePlace("generation-foreign-session-command");
        foreignCommand.expiresAtMs = expiry;
        foreignCommand.context.account = oldCommand.context.account;
        foreignCommand.context.executionDomain = oldCommand.context.executionDomain;
        foreignCommand.context.sessionId = "foreign-session";
        const auto foreign = coordinator.PlaceOrder(foreignCommand);
        assert(foreign.status == ExecutionCommandStatus::Accepted);
        assert(coordinator.RecordOrderTerminalDurably(foreign.orderId, &reason));
        assert(sends == 2);
    }

    // Exercise the exact v2 stopped-state producer
'''
require(t.count(old) == 1, "foreign-session fixture insertion mismatch")
t = t.replace(old, new, 1)
old = '''        assert(recovered.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        assert(sends == 1); // disk lookup never calls the venue

        auto newCommand = MakePlace("generation-new-command");
'''
new = '''        assert(recovered.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        ExecutionCommandResult foreignStatus;
        assert(recovered.GetCommandStatus(
            oldCommand.context.agentId, "foreign-session",
            "generation-foreign-session-command", foreignStatus));
        assert(foreignStatus.status == ExecutionCommandStatus::Accepted);

        OmsGenerationStore generationStore(path);
        std::string generationReason;
        assert(generationStore.Recover(
            64U * 1024U * 1024U, 65536U, 262144U,
            [](const OmsJournalEvent&) {}, generationReason));
        OmsGenerationMutationSummary ownerSummary;
        assert(generationStore.SummarizeMutationRecords(
            oldCommand.context.agentId, oldCommand.context.sessionId,
            oldCommand.context.account, oldCommand.context.executionDomain,
            ownerSummary, generationReason));
        assert(ownerSummary.commandCount == 1);
        OmsGenerationMutationSummary foreignSummary;
        assert(generationStore.SummarizeMutationRecords(
            oldCommand.context.agentId, "foreign-session",
            oldCommand.context.account, oldCommand.context.executionDomain,
            foreignSummary, generationReason));
        assert(foreignSummary.commandCount == 1);
        assert(ownerSummary.commandBindingSha256 !=
               foreignSummary.commandBindingSha256);
        assert(sends == 2); // disk lookup/history summary never calls venue

        auto newCommand = MakePlace("generation-new-command");
'''
require(t.count(old) == 1, "foreign-session summary assertions mismatch")
t = t.replace(old, new, 1)
needle = "        assert(sends == 2); // capacity was adopted, so new entry is not UNKNOWN"
require(t.count(needle) == 1, "post-recovery send assertion mismatch")
t = t.replace(
    needle,
    "        assert(sends == 3); // capacity was adopted, so new entry is not UNKNOWN", 1)
path.write_text(t)

# 4) Python regression: bound verifier memory and remove artificial chain depth ceiling.
path = Path("tests/python/test_oms_lifecycle_rotation.py")
p = path.read_text()
require("import tempfile\nimport unittest\n" in p, "test imports mismatch")
p = p.replace(
    "import tempfile\nimport unittest\n",
    "import tempfile\nimport tracemalloc\nimport unittest\nfrom unittest import mock\n", 1)
marker = "\n\n    def test_send_attempt_index_remains_window_sorted_across_generations(self) -> None:\n"
require(marker in p, "test method insertion marker missing")
methods = dedent(r'''
    def test_cumulative_runtime_index_validation_uses_bounded_memory(self) -> None:
        index_path = self.root / "large-runtime-command-index.tsv"
        rows = 50000
        with index_path.open("wb") as stream:
            for index in range(rows):
                fields = [
                    lifecycle.v1._hex("agent-a"), lifecycle.v1._hex("session-a"),
                    lifecycle.v1._hex(f"cmd-{index:08d}"),
                    lifecycle.v1._hex(f"hash-{index}"),
                    "place", "accepted", str(1000 + index),
                    lifecycle.v1._hex(""),
                    lifecycle.v1._hex(f"corr-{index}"), str(index + 1),
                    lifecycle.v1._hex("SIM-1"), lifecycle.v1._hex("SIM"), "1",
                ]
                stream.write(("\t".join(fields) + "\n").encode("ascii"))
        os.chmod(index_path, 0o600)
        tracemalloc.start()
        lifecycle._validate_runtime_index_stream(index_path, rows)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_generation_chain_has_no_artificial_1024_generation_ceiling(self) -> None:
        depth = 1100
        manifests = {}
        for index in range(depth):
            name = f"g{index}"
            parent = "" if index == 0 else f"g{index - 1}"
            manifests[name] = {
                "schema": lifecycle.SCHEMA,
                "generation": name,
                "parent_generation": parent,
                "parent_manifest_sha256": "a" * 64 if parent else "",
            }
        with mock.patch.object(
                lifecycle, "_manifest_for",
                side_effect=lambda store, name: manifests[name]), \
             mock.patch.object(lifecycle.v1, "_hash_file", return_value="a" * 64):
            chain = lifecycle._generation_chain(self.store, f"g{depth - 1}")
        self.assertEqual(len(chain), depth)
        self.assertEqual(chain[0][0], "g0")
        self.assertEqual(chain[-1][0], f"g{depth - 1}")


''')
p = p.replace(marker, "\n\n" + methods + "    def test_send_attempt_index_remains_window_sorted_across_generations(self) -> None:\n", 1)
path.write_text(p)

# 5) Keep a single canonical Python development runner.
path = Path("docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md")
d = path.read_text()
old = "./scripts/dev_core.sh\npython3 scripts/check_documentation.py\npython3 -m unittest discover -s tests/python -p 'test_*.py'"
new = "./scripts/dev_core.sh\npython3 scripts/check_documentation.py\npython3 scripts/run_python_tests.py --lane core\npython3 scripts/run_python_tests.py --lane source"
require(d.count(old) == 1, "architecture development command block mismatch")
path.write_text(d.replace(old, new, 1))

print("patches applied")
