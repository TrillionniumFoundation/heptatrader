from pathlib import Path

p = Path("scripts/.full_gap_patch.py")
s = p.read_text()

# Preserve the four-space class-member indentation in the C++ header anchor.
start = s.index("old = dedent('''\n    bool EnumerateMutationRecords(")
end = s.index("require(h.count(old)", start)
replacement = '''old = """    bool EnumerateMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        std::vector<OmsGenerationMutationRecord>& records,
        std::string& reason) const;

    bool SummarizeMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        OmsGenerationMutationSummary& summary,
        std::string& reason) const;"""
new = """    bool EnumerateMutationRecords(
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
        std::string& reason) const;"""
'''
s = s[:start] + replacement + s[end:]

# PR #95 combines cycle detection and the old fixed chain ceiling in one branch.
old = '''chain_limit = '        if len(chain) >= MAX_CHAIN:\\n            raise v1.GenerationError("OMS_GENERATION_CHAIN_LIMIT")\\n'
require(chain_limit in text, "generation chain ceiling block missing")
text = text.replace(chain_limit, "", 1)
'''
new = '''chain_limit = '        if generation in seen or len(chain) >= MAX_CHAIN:\\n            raise v1.GenerationError("OMS_GENERATION_PARENT_CHAIN_INVALID")\\n'
require(chain_limit in text, "generation chain ceiling block missing")
text = text.replace(
    chain_limit,
    '        if generation in seen:\\n            raise v1.GenerationError("OMS_GENERATION_PARENT_CHAIN_INVALID")\\n',
    1)
'''
if old not in s:
    raise SystemExit("materializer chain-limit source block missing")
s = s.replace(old, new, 1)

# The verifier block is followed by current-generation checks, not a top-level
# journal check, and reads counts directly from the manifest.
old = '''start = text.find('    runtime_lines = list(_iter_private_lines(root / "runtime-command-index.tsv"))')
end = text.find("\\n    if journal is not None:", start)
require(start >= 0 and end > start, "verify_generation cumulative index block missing")
replacement = ''' + "'''" + '''    _validate_runtime_index_stream(
        root / "runtime-command-index.tsv", command_records)
    _validate_send_index_stream(
        root / "send-attempt-index.tsv", send_attempt_records,
        ordered=send_index_order == SEND_INDEX_ORDER)
''' + "'''" + '''
text = text[:start] + replacement + text[end:]
'''
new = '''start = text.find('    runtime_lines = list(_iter_private_lines(root / "runtime-command-index.tsv"))')
end = text.find('\\n    if current and current["generation"] == generation:', start)
require(start >= 0 and end > start, "verify_generation cumulative index block missing")
replacement = ''' + "'''" + '''    _validate_runtime_index_stream(
        root / "runtime-command-index.tsv", manifest.get("command_records"))
    _validate_send_index_stream(
        root / "send-attempt-index.tsv", manifest.get("send_attempt_records"),
        ordered=sorted_send_index)
''' + "'''" + '''
text = text[:start] + replacement + text[end:]
'''
if old not in s:
    raise SystemExit("materializer verifier source block missing")
s = s.replace(old, new, 1)

# HPM2's summary scans the complete permanent command index. Non-mutation
# records (for example durable owner terminalization witnesses) are valid rows
# and must be ignored, not rejected. Only rows declaring durable mutation intent
# must carry a place/cancel/flatten operation.
needle = '''sum_block = sum_block.replace(old_decode, new_decode, 1)
require(sum_block.count(old_filter) == 1, "summary filter mismatch")
'''
replacement = '''sum_block = sum_block.replace(old_decode, new_decode, 1)
summary_operation_guard = ''' + "'''" + '''            (fields[12] != "0" && fields[12] != "1") ||
            (fields[4] != "place" && fields[4] != "cancel" &&
             fields[4] != "flatten"))
''' + "'''" + '''
summary_operation_guard_fixed = ''' + "'''" + '''            (fields[12] != "0" && fields[12] != "1") ||
            (fields[12] == "1" && fields[4] != "place" &&
             fields[4] != "cancel" && fields[4] != "flatten"))
''' + "'''" + '''
require(sum_block.count(summary_operation_guard) == 1, "summary operation guard mismatch")
sum_block = sum_block.replace(
    summary_operation_guard, summary_operation_guard_fixed, 1)
require(sum_block.count(old_filter) == 1, "summary filter mismatch")
'''
if needle not in s:
    raise SystemExit("materializer summary guard insertion point missing")
s = s.replace(needle, replacement, 1)

# Match the actual explanatory suffix on the native fixture comment in PR #95.
needle = "    // Exercise the exact v2 stopped-state producer\n"
if s.count(needle) != 2:
    raise SystemExit(f"materializer native comment count={s.count(needle)}")
s = s.replace(
    needle,
    "    // Exercise the exact v2 stopped-state producer after the real writer has\n")
p.write_text(s)
