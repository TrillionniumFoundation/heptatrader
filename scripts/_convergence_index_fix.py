#!/usr/bin/env python3
from pathlib import Path


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


# New generations must not manufacture command-index rows from keyed owner,
# terminal, fence or projection events which do not establish a mutation
# operation. They still remain in the journal/hot replay and may update a real
# command row when that command already exists.
path = Path("scripts/hepta_oms_checkpoint.py")
text = path.read_text()
text = replace_one(text,
'''    key = _key(event)
    if not all(key):
        return
    record = commands.setdefault(key, {
''',
'''    key = _key(event)
    if not all(key):
        return
    if key not in commands and not _operation(event):
        return
    record = commands.setdefault(key, {
''', "checkpoint auxiliary command admission")
path.write_text(text)

# V2 verification accepts legacy non-mutation rows only when they never carried
# a durable mutation intent. The next seal drops those compatibility rows while
# preserving every real command identity.
path = Path("scripts/hepta_oms_lifecycle.py")
text = path.read_text()
text = replace_one(text,
'''    if record["operation"] not in {"place", "cancel", "flatten"} or record["status"] not in {"accepted", "rejected", "uncertain"}:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    return (fields[0], fields[1], fields[2]), record, fields
''',
'''    mutation_operation = record["operation"] in {"place", "cancel", "flatten"}
    mutation_status = record["status"] in {"accepted", "rejected", "uncertain"}
    if record["durable_mutation_intent"] and not (mutation_operation and mutation_status):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    if record["operation"] and not mutation_operation:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    if record["status"] not in {"unknown", "accepted", "rejected", "uncertain"}:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    record["mutation_record"] = mutation_operation and mutation_status
    return (fields[0], fields[1], fields[2]), record, fields
''', "lifecycle legacy auxiliary parser")
text = replace_one(text,
'''            if updates_index < len(ordered_updates) and ordered_updates[updates_index][0] == key:
                record = _merge_command_record(parent_record, ordered_updates[updates_index][1])
                encoded = v1._runtime_index_line(record)
                updates_index += 1
            else:
                encoded = line
            v1._write_all(runtime_fd, encoded)
''',
'''            if updates_index < len(ordered_updates) and ordered_updates[updates_index][0] == key:
                update = ordered_updates[updates_index][1]
                record = (_merge_command_record(parent_record, update)
                          if parent_record.get("mutation_record") else update)
                encoded = v1._runtime_index_line(record)
                updates_index += 1
            else:
                if not parent_record.get("mutation_record"):
                    continue
                encoded = line
            v1._write_all(runtime_fd, encoded)
''', "lifecycle legacy auxiliary compaction")
path.write_text(text)

# Native readers must remain compatible with an already-sealed pre-fix
# generation. An auxiliary row is not a command-status identity and is ignored;
# an invalid row claiming durable mutation intent still fails closed.
path = Path("HeptaTrade/execution/execution_generation_support.cpp")
text = path.read_text()
text = replace_one(text,
'''            record.agentId != agentId || record.sessionId != sessionId ||
            record.commandId != commandId ||
            (fields[4] != "place" && fields[4] != "cancel" &&
             fields[4] != "flatten") ||
            (fields[5] != "accepted" && fields[5] != "rejected" &&
             fields[5] != "uncertain"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return OmsGenerationLookupStatus::Error;
        }
''',
'''            record.agentId != agentId || record.sessionId != sessionId ||
            record.commandId != commandId)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return OmsGenerationLookupStatus::Error;
        }
        const bool mutationOperation = fields[4] == "place" ||
            fields[4] == "cancel" || fields[4] == "flatten";
        const bool mutationStatus = fields[5] == "accepted" ||
            fields[5] == "rejected" || fields[5] == "uncertain";
        if (!mutationOperation || !mutationStatus)
        {
            if (fields[12] == "1" || !fields[4].empty() ||
                (fields[5] != "unknown" && !fields[5].empty()))
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                return OmsGenerationLookupStatus::Error;
            }
            reason.clear();
            return OmsGenerationLookupStatus::Missing;
        }
''', "native legacy auxiliary lookup")
# In the compact terminal summary, compatibility auxiliary rows are ignored;
# durable rows with invalid mutation semantics still poison the generation.
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
        if (!GenerationDecodeHex(fields[0], rowAgent) ||
            !GenerationDecodeHex(fields[1], rowSession) ||
            !GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
            (fields[12] != "0" && fields[12] != "1"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            ok = false;
            break;
        }
        const bool mutationOperation = fields[4] == "place" ||
            fields[4] == "cancel" || fields[4] == "flatten";
        const bool mutationStatus = fields[5] == "accepted" ||
            fields[5] == "rejected" || fields[5] == "uncertain";
        if (!mutationOperation || !mutationStatus)
        {
            if (fields[12] == "1" || !fields[4].empty() ||
                (fields[5] != "unknown" && !fields[5].empty()))
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                ok = false;
                break;
            }
            offset = end;
            continue;
        }
'''
if text.count(old) != 1:
    raise SystemExit(f"native summary compatibility: expected one match, got {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text)

# Tighten the existing generation regression: owner-terminal evidence stays in
# the journal/hot projection but is no longer counted as a permanent command.
path = Path("tests/python/test_oms_checkpoint.py")
text = path.read_text()
text = replace_one(text, '        self.assertEqual(verified["command_records"], 3)\n',
                   '        self.assertEqual(verified["command_records"], 2)\n',
                   "checkpoint command count")
text = replace_one(text,
'''        missing = lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "never-seen", "hash")
''',
'''        missing = lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "never-seen", "hash")
        auxiliary = lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "owner-terminal", "hash-owner")
''', "checkpoint auxiliary lookup")
text = replace_one(text,
'''        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(missing["status"], "missing")
''',
'''        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(auxiliary["status"], "missing")
''', "checkpoint auxiliary assertion")
path.write_text(text)
