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
p.write_text(s)
