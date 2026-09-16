#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    value = text(path)
    count = value.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    write(path, value.replace(old, new, 1))


# 1. Remove explicitly temporary duplicate diagnostics from Core CI. The same
# tests remain owned by run_python_tests.py / accept_core_release.py.
path = ".github/workflows/core-ci.yml"
value = text(path)
pattern = re.compile(
    r"\n      # Temporary isolation for PR #88\.[\s\S]*?"
    r"\n      - name: Accept the exact core artifact through installed processes and systemd"
)
replacement = "\n      - name: Accept the exact core artifact through installed processes and systemd"
value, count = pattern.subn(replacement, value, count=1)
if count != 1:
    raise RuntimeError(f"{path}: temporary diagnostic block not found")
write(path, value)

# 2. Record whether the pinned cumulative send index is window-sorted. V1
# producers already use this order; new V2 generations now preserve it.
replace_once(
    "HeptaTrade/oms_generation_store.h",
    "    bool m_active = false;\n    bool m_segmentedTail = false;\n",
    "    bool m_active = false;\n    bool m_segmentedTail = false;\n"
    "    bool m_sendIndexWindowSorted = false;\n",
)

replace_once(
    "HeptaTrade/execution/execution_generation_support.inc",
    "    m_active = false;\n    m_generation.clear();\n",
    "    m_active = false;\n    m_sendIndexWindowSorted = false;\n    m_generation.clear();\n",
)

# Replace the historical full-file scan with a sorted-index lower-bound query,
# retaining the old scan for pre-change V2 generations that lack an order tag.
old_read = '''bool OmsGenerationStore::ReadPlaceSendAttemptTimes(
    const std::string& account,
    const std::string& executionDomain,
    std::int64_t cutoffMs,
    const std::set<std::string>& excludedRequestKeys,
    std::vector<OmsGenerationSendAttempt>& attempts,
    std::string& reason) const
{
    attempts.clear();
    if (!m_active) return true;
    if (!ValidatePinnedIndex(m_sendIndexFd, "send-attempt-index.tsv",
            m_sendIndexIdentity))
    {
        reason = "OMS_GENERATION_SEND_INDEX_CHANGED";
        return false;
    }
    off_t offset = 0;
    while (offset < m_sendIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_sendIndexFd,
                m_sendIndexIdentity.st_size, offset, start, end, line) ||
            start != offset || end <= offset ||
            !GenerationSplitTabs(line, fields) || fields.size() != 7U)
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        std::string rowAccount, rowDomain, agent, session, command;
        std::int64_t ts = 0;
        std::uint64_t sequence = 0;
        if (!GenerationDecodeHex(fields[0], rowAccount) ||
            !GenerationDecodeHex(fields[1], rowDomain) ||
            !GenerationParseSigned64(fields[2], ts) ||
            !GenerationDecodeHex(fields[3], agent) ||
            !GenerationDecodeHex(fields[4], session) ||
            !GenerationDecodeHex(fields[5], command) ||
            !GenerationParseUnsigned(fields[6], sequence))
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        if (rowAccount == account && rowDomain == executionDomain && ts > cutoffMs)
        {
            const std::string requestKey = GenerationRequestKey(agent, session, command);
            if (excludedRequestKeys.find(requestKey) == excludedRequestKeys.end())
            {
                OmsGenerationSendAttempt attempt;
                attempt.requestKey = requestKey;
                attempt.tsMs = ts;
                attempt.sequence = sequence;
                attempts.push_back(attempt);
            }
        }
        offset = end;
    }
    std::sort(attempts.begin(), attempts.end(),
        [](const OmsGenerationSendAttempt& left,
           const OmsGenerationSendAttempt& right) {
            return left.sequence < right.sequence;
        });
    reason.clear();
    return true;
}
'''
new_read = '''bool OmsGenerationStore::ReadPlaceSendAttemptTimes(
    const std::string& account,
    const std::string& executionDomain,
    std::int64_t cutoffMs,
    const std::set<std::string>& excludedRequestKeys,
    std::vector<OmsGenerationSendAttempt>& attempts,
    std::string& reason) const
{
    attempts.clear();
    if (!m_active) return true;
    if (!ValidatePinnedIndex(m_sendIndexFd, "send-attempt-index.tsv",
            m_sendIndexIdentity))
    {
        reason = "OMS_GENERATION_SEND_INDEX_CHANGED";
        return false;
    }

    const std::string wantedAccount = GenerationHex(account);
    const std::string wantedDomain = GenerationHex(executionDomain);
    const auto parseRow = [](const std::vector<std::string>& fields,
                             std::string& rowAccount,
                             std::string& rowDomain,
                             std::int64_t& ts,
                             std::string& agent,
                             std::string& session,
                             std::string& command,
                             std::uint64_t& sequence) {
        return fields.size() == 7U &&
            GenerationDecodeHex(fields[0], rowAccount) &&
            GenerationDecodeHex(fields[1], rowDomain) &&
            GenerationParseSigned64(fields[2], ts) &&
            GenerationDecodeHex(fields[3], agent) &&
            GenerationDecodeHex(fields[4], session) &&
            GenerationDecodeHex(fields[5], command) &&
            GenerationParseUnsigned(fields[6], sequence);
    };

    off_t offset = 0;
    if (m_sendIndexWindowSorted && m_sendIndexIdentity.st_size > 0)
    {
        // The index is ordered by encoded account, encoded execution domain,
        // signed timestamp, durable sequence, then request identity. Locate the
        // first row in the requested account/domain with timestamp > cutoff;
        // normal rate checks therefore touch O(log history + window) rows.
        off_t low = 0;
        off_t high = m_sendIndexIdentity.st_size;
        while (low < high)
        {
            const off_t midpoint = low + (high - low) / 2;
            off_t start = 0, end = 0;
            std::string line;
            std::vector<std::string> fields;
            std::string rowAccount, rowDomain, agent, session, command;
            std::int64_t ts = 0;
            std::uint64_t sequence = 0;
            if (!GenerationReadLineContaining(m_sendIndexFd,
                    m_sendIndexIdentity.st_size, midpoint, start, end, line) ||
                !GenerationSplitTabs(line, fields) ||
                !parseRow(fields, rowAccount, rowDomain, ts,
                    agent, session, command, sequence))
            {
                reason = "OMS_GENERATION_SEND_INDEX_INVALID";
                return false;
            }
            const bool beforeWindow =
                fields[0] < wantedAccount ||
                (fields[0] == wantedAccount && fields[1] < wantedDomain) ||
                (fields[0] == wantedAccount && fields[1] == wantedDomain &&
                 ts <= cutoffMs);
            if (beforeWindow)
            {
                if (end <= low)
                {
                    reason = "OMS_GENERATION_SEND_INDEX_INVALID";
                    return false;
                }
                low = end;
            }
            else
            {
                if (start >= high && high != 0)
                {
                    reason = "OMS_GENERATION_SEND_INDEX_INVALID";
                    return false;
                }
                high = start;
            }
        }
        offset = low;
    }

    while (offset < m_sendIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_sendIndexFd,
                m_sendIndexIdentity.st_size, offset, start, end, line) ||
            start != offset || end <= offset ||
            !GenerationSplitTabs(line, fields))
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        std::string rowAccount, rowDomain, agent, session, command;
        std::int64_t ts = 0;
        std::uint64_t sequence = 0;
        if (!parseRow(fields, rowAccount, rowDomain, ts,
                agent, session, command, sequence))
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        if (m_sendIndexWindowSorted)
        {
            if (fields[0] != wantedAccount || fields[1] != wantedDomain)
                break;
            if (ts <= cutoffMs)
            {
                reason = "OMS_GENERATION_SEND_INDEX_ORDER_INVALID";
                return false;
            }
        }
        if (rowAccount == account && rowDomain == executionDomain && ts > cutoffMs)
        {
            const std::string requestKey = GenerationRequestKey(agent, session, command);
            if (excludedRequestKeys.find(requestKey) == excludedRequestKeys.end())
            {
                OmsGenerationSendAttempt attempt;
                attempt.requestKey = requestKey;
                attempt.tsMs = ts;
                attempt.sequence = sequence;
                attempts.push_back(attempt);
            }
        }
        offset = end;
    }
    std::sort(attempts.begin(), attempts.end(),
        [](const OmsGenerationSendAttempt& left,
           const OmsGenerationSendAttempt& right) {
            return left.sequence < right.sequence;
        });
    reason.clear();
    return true;
}
'''
replace_once("HeptaTrade/execution/execution_generation_support.inc", old_read, new_read)

# V2 runtime manifests explicitly declare the ordering property. Old V2
# manifests are still readable and use the compatibility scan.
replace_once(
    "HeptaTrade/execution/execution_generation_v2_support.inc",
    '''    if (PrepareGenerationV1(v1Reason))
    {
        reason.clear();
        return true;
    }
''',
    '''    if (PrepareGenerationV1(v1Reason))
    {
        m_sendIndexWindowSorted = true;
        reason.clear();
        return true;
    }
''',
)

replace_once(
    "HeptaTrade/execution/execution_generation_v2_support.inc",
    '''    static const char* const manifestNames[] = {
        "generation", "parent_generation", "parent_manifest_sha256",
        "history_records", "segment_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes",
        "active_tail_header_sha256", "authorization_effect",
        "paper_authorized", "live_authorized"
    };
    std::uint64_t historyRecords = 0, segmentRecords = 0;
    if (!GenerationParseFields(runtimeManifest, kRuntimeManifestHeaderV2, manifest) ||
        !GenerationExactFieldNames(manifest, manifestNames,
            sizeof(manifestNames) / sizeof(manifestNames[0])) ||
''',
    '''    static const char* const manifestNamesLegacy[] = {
        "generation", "parent_generation", "parent_manifest_sha256",
        "history_records", "segment_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes",
        "active_tail_header_sha256", "authorization_effect",
        "paper_authorized", "live_authorized"
    };
    static const char* const manifestNamesSorted[] = {
        "generation", "parent_generation", "parent_manifest_sha256",
        "history_records", "segment_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "send_attempt_index_order", "hot_replay_sha256",
        "active_tail_header_bytes", "active_tail_header_sha256",
        "authorization_effect", "paper_authorized", "live_authorized"
    };
    std::uint64_t historyRecords = 0, segmentRecords = 0;
    if (!GenerationParseFields(runtimeManifest, kRuntimeManifestHeaderV2, manifest))
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_V2_INVALID";
        Close();
        return false;
    }
    const bool sortedSendIndex = GenerationExactFieldNames(
        manifest, manifestNamesSorted,
        sizeof(manifestNamesSorted) / sizeof(manifestNamesSorted[0]));
    const bool legacySendIndex = GenerationExactFieldNames(
        manifest, manifestNamesLegacy,
        sizeof(manifestNamesLegacy) / sizeof(manifestNamesLegacy[0]));
    if ((!sortedSendIndex && !legacySendIndex) ||
        (sortedSendIndex &&
         manifest["send_attempt_index_order"] != "account-domain-time-v1") ||
''',
)

replace_once(
    "HeptaTrade/execution/execution_generation_v2_support.inc",
    '''    m_journalPrefixSha256 = manifest["active_tail_header_sha256"];
''',
    '''    m_journalPrefixSha256 = manifest["active_tail_header_sha256"];
    m_sendIndexWindowSorted = sortedSendIndex;
''',
)

# Keep the V2 cumulative send index globally sorted rather than concatenating
# parent history with each new tail.
replace_once(
    "scripts/hepta_oms_lifecycle.py",
    '''    send_path = generation_dir / "send-attempt-index.tsv"
    send_fd = os.open(send_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    send_count = 0
    try:
        if parent_dir is not None:
            for line in _iter_private_lines(parent_dir / "send-attempt-index.tsv"):
                if len(line) > v1.MAX_INDEX_LINE or len(line.rstrip(b"\\n").split(b"\\t")) != 7:
                    raise v1.GenerationError("OMS_GENERATION_PARENT_SEND_INDEX_INVALID")
                v1._write_all(send_fd, line)
                send_count += 1
        for attempt in tail_attempts:
            copied = dict(attempt)
            copied["sequence"] = history_base + int(copied["sequence"])
            v1._write_all(send_fd, v1._send_attempt_line(copied))
            send_count += 1
        v1._fsync(send_fd)
    finally:
        os.close(send_fd)
''',
    '''    def send_key(line: bytes) -> tuple[str, str, int, int, str, str, str]:
        if len(line) > v1.MAX_INDEX_LINE:
            raise v1.GenerationError("OMS_GENERATION_PARENT_SEND_INDEX_INVALID")
        fields = line.rstrip(b"\\n").decode("ascii").split("\\t")
        if len(fields) != 7:
            raise v1.GenerationError("OMS_GENERATION_PARENT_SEND_INDEX_INVALID")
        try:
            timestamp = int(fields[2])
            sequence = int(fields[6])
            v1._unhex(fields[0]); v1._unhex(fields[1])
            v1._unhex(fields[3]); v1._unhex(fields[4]); v1._unhex(fields[5])
        except (ValueError, v1.GenerationError) as error:
            raise v1.GenerationError("OMS_GENERATION_PARENT_SEND_INDEX_INVALID") from error
        return (fields[0], fields[1], timestamp, sequence,
                fields[3], fields[4], fields[5])

    tail_lines: list[tuple[tuple[str, str, int, int, str, str, str], bytes]] = []
    for attempt in tail_attempts:
        copied = dict(attempt)
        copied["sequence"] = history_base + int(copied["sequence"])
        line = v1._send_attempt_line(copied)
        tail_lines.append((send_key(line), line))
    tail_lines.sort(key=lambda item: item[0])

    send_path = generation_dir / "send-attempt-index.tsv"
    send_fd = os.open(send_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    send_count = 0
    try:
        parent_iter = iter(()) if parent_dir is None else iter(
            _iter_private_lines(parent_dir / "send-attempt-index.tsv"))
        try:
            parent_line = next(parent_iter)
        except StopIteration:
            parent_line = None
        parent_key = send_key(parent_line) if parent_line is not None else None
        previous_parent = None
        tail_index = 0
        while parent_line is not None or tail_index < len(tail_lines):
            if parent_key is not None and previous_parent is not None and parent_key <= previous_parent:
                raise v1.GenerationError("OMS_GENERATION_PARENT_SEND_INDEX_ORDER_INVALID")
            take_parent = parent_line is not None and (
                tail_index >= len(tail_lines) or parent_key < tail_lines[tail_index][0])
            if take_parent:
                v1._write_all(send_fd, parent_line)
                send_count += 1
                previous_parent = parent_key
                try:
                    parent_line = next(parent_iter)
                    parent_key = send_key(parent_line)
                except StopIteration:
                    parent_line = None
                    parent_key = None
            else:
                if (parent_key is not None and tail_index < len(tail_lines) and
                        parent_key == tail_lines[tail_index][0]):
                    raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_DUPLICATE")
                v1._write_all(send_fd, tail_lines[tail_index][1])
                send_count += 1
                tail_index += 1
        v1._fsync(send_fd)
    finally:
        os.close(send_fd)
''',
)

replace_once(
    "scripts/hepta_oms_lifecycle.py",
    '''        f"send_attempt_index_sha256={digests['send-attempt-index.tsv']}",
        f"hot_replay_sha256={digests['hot-replay.jsonl']}",
''',
    '''        f"send_attempt_index_sha256={digests['send-attempt-index.tsv']}",
        "send_attempt_index_order=account-domain-time-v1",
        f"hot_replay_sha256={digests['hot-replay.jsonl']}",
''',
)

# Add a regression that forces cross-generation account interleaving and clock
# regression; concatenation would fail this ordering assertion.
path = "tests/python/test_oms_lifecycle_rotation.py"
value = text(path)
insert = '''
    def test_send_attempt_index_remains_window_sorted_across_generations(self) -> None:
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["send_attempt_records"], 1)
        values = command_events("second", 900, 202)
        for item in values:
            item["account"] = "AAA"
            item["execution_domain"] = "PAPER"
        self.append(values)
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        generation = second["generation"]
        rows = []
        for line in (self.store / generation / "send-attempt-index.tsv").read_text().splitlines():
            fields = line.split("\\t")
            rows.append((fields[0], fields[1], int(fields[2]), int(fields[6]),
                         fields[3], fields[4], fields[5]))
        self.assertEqual(rows, sorted(rows))
        runtime = (self.store / generation / "runtime-manifest.txt").read_text()
        self.assertIn("send_attempt_index_order=account-domain-time-v1\\n", runtime)
        lifecycle.verify_generation(self.store, journal=self.journal)

'''
marker = '\n\nif __name__ == "__main__":\n'
if marker not in value:
    raise RuntimeError(f"{path}: unittest marker missing")
write(path, value.replace(marker, "\n" + insert + marker, 1))

print("priority remediation batch 1 applied")
