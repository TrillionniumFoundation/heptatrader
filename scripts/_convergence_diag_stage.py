#!/usr/bin/env python3
from pathlib import Path

path = Path("HeptaTrade/execution/execution_generation_support.cpp")
text = path.read_text()
old = '''    if (!m_generationStore.SummarizeMutationRecords(
            binding.owner.agentId, binding.owner.sessionId,
            binding.owner.account, binding.owner.executionDomain,
            sealed, reason))
        return false;
'''
new = '''    if (!m_generationStore.SummarizeMutationRecords(
            binding.owner.agentId, binding.owner.sessionId,
            binding.owner.account, binding.owner.executionDomain,
            sealed, reason))
    {
        reason = "HPM2_SEALED_SUMMARY:" + reason;
        return false;
    }
'''
if text.count(old) != 1:
    raise SystemExit("summary diagnostic match failed")
text = text.replace(old, new, 1)
old = '''        std::string rowAgent, rowSession, rowAccount, rowDomain;
        if (!GenerationDecodeHex(fields[0], rowAgent) ||
            !GenerationDecodeHex(fields[1], rowSession) ||
            !GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
            (fields[12] != "0" && fields[12] != "1") ||
            (fields[4] != "place" && fields[4] != "cancel" &&
             fields[4] != "flatten"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            ok = false;
            break;
        }
'''
new = '''        std::string rowAgent, rowSession, rowAccount, rowDomain;
        if (!GenerationDecodeHex(fields[0], rowAgent))
        { reason = "OMS_GENERATION_COMMAND_INDEX_AGENT_HEX_INVALID"; ok = false; break; }
        if (!GenerationDecodeHex(fields[1], rowSession))
        { reason = "OMS_GENERATION_COMMAND_INDEX_SESSION_HEX_INVALID"; ok = false; break; }
        if (!GenerationDecodeHex(fields[10], rowAccount))
        { reason = "OMS_GENERATION_COMMAND_INDEX_ACCOUNT_HEX_INVALID"; ok = false; break; }
        if (!GenerationDecodeHex(fields[11], rowDomain))
        { reason = "OMS_GENERATION_COMMAND_INDEX_DOMAIN_HEX_INVALID"; ok = false; break; }
        if (fields[12] != "0" && fields[12] != "1")
        { reason = "OMS_GENERATION_COMMAND_INDEX_INTENT_INVALID"; ok = false; break; }
        if (fields[4] != "place" && fields[4] != "cancel" && fields[4] != "flatten")
        { reason = "OMS_GENERATION_COMMAND_INDEX_OPERATION_INVALID:" + fields[4]; ok = false; break; }
'''
if text.count(old) != 1:
    raise SystemExit("summary field diagnostic match failed")
text = text.replace(old, new, 1)
old = '''        if (lookup == OmsGenerationLookupStatus::Error)
        {
            reason = lookupReason.empty() ?
                "OMS_GENERATION_COMMAND_INDEX_FAILED" : lookupReason;
            return false;
        }
'''
new = '''        if (lookup == OmsGenerationLookupStatus::Error)
        {
            reason = std::string("HPM2_ACTIVE_LOOKUP:") +
                (lookupReason.empty() ? "OMS_GENERATION_COMMAND_INDEX_FAILED" : lookupReason);
            return false;
        }
'''
if text.count(old) != 1:
    raise SystemExit("lookup diagnostic match failed")
path.write_text(text.replace(old, new, 1))
