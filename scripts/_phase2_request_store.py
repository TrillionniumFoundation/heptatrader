#!/usr/bin/env python3
from pathlib import Path
import re


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


header = Path("HeptaTrade/execution/execution_coordinator.h")
text = header.read_text()
old = '''    // Hot records remain ordinary unordered-map entries. A miss consults the
    // immutable full-key generation index and materializes at most a small LRU
    // cache of terminal historical records. operator[] promotes a cached record
    // before it can be changed by an active-tail event.
    class RequestRecordStore : public std::unordered_map<std::string, RequestRecord>
    {
    public:
        typedef std::unordered_map<std::string, RequestRecord> Base;
        explicit RequestRecordStore(OmsGenerationStore* generationStore = nullptr);
        Base::iterator find(const std::string& key);
        Base::const_iterator find(const std::string& key) const;
        RequestRecord& operator[](const std::string& key);
        void clear();
        std::size_t HotSize() const;

    private:
        Base::iterator LoadHistorical(const std::string& key);
        static bool DecodeRequestKey(const std::string& key,
                                     std::string& agentId,
                                     std::string& sessionId,
                                     std::string& commandId);
        void RememberHistorical(const std::string& key);
        void Promote(const std::string& key);

    private:
        OmsGenerationStore* m_generationStore = nullptr;
        std::deque<std::string> m_historicalOrder;
        std::unordered_set<std::string> m_historicalKeys;
    };
'''
new = '''    // Hot command state and disk-backed historical identity are deliberately
    // composed rather than disguised as an unordered_map subclass. Find() may
    // consult the pinned generation index; hot iteration never performs I/O.
    class RequestRecordStore
    {
    public:
        typedef std::unordered_map<std::string, RequestRecord> Base;
        typedef Base::iterator iterator;
        typedef Base::const_iterator const_iterator;
        explicit RequestRecordStore(OmsGenerationStore* generationStore = nullptr);
        iterator Find(const std::string& key);
        const_iterator Find(const std::string& key) const;
        RequestRecord& GetMutable(const std::string& key);
        iterator HotBegin() { return m_records.begin(); }
        iterator HotEnd() { return m_records.end(); }
        const_iterator HotBegin() const { return m_records.begin(); }
        const_iterator HotEnd() const { return m_records.end(); }
        void Clear();
        std::size_t HotSize() const;

    private:
        iterator LoadHistorical(const std::string& key);
        static bool DecodeRequestKey(const std::string& key,
                                     std::string& agentId,
                                     std::string& sessionId,
                                     std::string& commandId);
        void RememberHistorical(const std::string& key);
        void Promote(const std::string& key);

    private:
        Base m_records;
        OmsGenerationStore* m_generationStore = nullptr;
        std::deque<std::string> m_historicalOrder;
        std::unordered_set<std::string> m_historicalKeys;
    };
'''
text = replace_one(text, old, new, "request store class")
header.write_text(text)

impl = Path("HeptaTrade/execution/execution_generation_support.cpp")
text = impl.read_text()
start = text.index("ExecutionCoordinator::RequestRecordStore::RequestRecordStore(")
end = text.index("bool ExecutionCoordinator::EnterPaperTerminalFenceAndProjectGenerationAwareLocked(", start)
segment = text[start:end]
segment = segment.replace("Base::erase(oldest)", "m_records.erase(oldest)")
segment = segment.replace("RequestRecordStore::Base::iterator\nExecutionCoordinator::RequestRecordStore::LoadHistorical".replace("\\n", "\n"),
                          "RequestRecordStore::iterator\nExecutionCoordinator::RequestRecordStore::LoadHistorical".replace("\\n", "\n"))
segment = segment.replace("Base::iterator existing = Base::find(key);", "iterator existing = m_records.find(key);")
segment = segment.replace("if (existing != Base::end()) return existing;", "if (existing != m_records.end()) return existing;")
segment = segment.replace("return Base::end();", "return m_records.end();")
segment = segment.replace("const std::pair<Base::iterator, bool> inserted =\n        Base::insert(std::make_pair(key, record));".replace("\\n", "\n"),
                          "const std::pair<iterator, bool> inserted =\n        m_records.insert(std::make_pair(key, record));".replace("\\n", "\n"))
segment = segment.replace("RequestRecordStore::Base::iterator\nExecutionCoordinator::RequestRecordStore::find".replace("\\n", "\n"),
                          "RequestRecordStore::iterator\nExecutionCoordinator::RequestRecordStore::Find".replace("\\n", "\n"))
segment = segment.replace("RequestRecordStore::Base::const_iterator\nExecutionCoordinator::RequestRecordStore::find".replace("\\n", "\n"),
                          "RequestRecordStore::const_iterator\nExecutionCoordinator::RequestRecordStore::Find".replace("\\n", "\n"))
segment = segment.replace("Base::const_iterator existing = Base::find(key);", "const_iterator existing = m_records.find(key);")
segment = segment.replace("RequestRecordStore* self = const_cast<RequestRecordStore*>(this);\n    const Base::iterator loaded = self->LoadHistorical(key);\n    return loaded == self->Base::end() ? Base::end() : loaded;".replace("\\n", "\n"),
                          "RequestRecordStore* self = const_cast<RequestRecordStore*>(this);\n    const iterator loaded = self->LoadHistorical(key);\n    return loaded == self->m_records.end() ? m_records.end() : loaded;".replace("\\n", "\n"))
segment = segment.replace("ExecutionCoordinator::RequestRecordStore::operator[]", "ExecutionCoordinator::RequestRecordStore::GetMutable")
segment = segment.replace("Base::iterator existing = find(key);", "iterator existing = Find(key);")
segment = segment.replace("if (existing != Base::end())", "if (existing != m_records.end())")
segment = segment.replace("return Base::operator[](key);", "return m_records[key];")
segment = segment.replace("ExecutionCoordinator::RequestRecordStore::clear()", "ExecutionCoordinator::RequestRecordStore::Clear()")
segment = segment.replace("Base::clear();", "m_records.clear();")
segment = segment.replace("return Base::size() >= m_historicalKeys.size() ?\n        Base::size() - m_historicalKeys.size() : 0U;".replace("\\n", "\n"),
                          "return m_records.size() >= m_historicalKeys.size() ?\n        m_records.size() - m_historicalKeys.size() : 0U;".replace("\\n", "\n"))
for forbidden in ("Base::find", "Base::end", "Base::insert", "Base::erase", "Base::operator[]", "Base::clear", "Base::size", "RequestRecordStore::find", "RequestRecordStore::operator[]"):
    if forbidden in segment:
        raise SystemExit(f"request store implementation still exposes inherited-map behavior: {forbidden}")
text = text[:start] + segment + text[end:]
impl.write_text(text)

paths = sorted(Path("HeptaTrade/execution").glob("*.cpp"))
for path in paths:
    text = path.read_text()
    text = text.replace("m_requests.find(", "m_requests.Find(")
    text = text.replace("m_requests.begin()", "m_requests.HotBegin()")
    text = text.replace("m_requests.end()", "m_requests.HotEnd()")
    text = text.replace("m_requests.clear()", "m_requests.Clear()")
    text = re.sub(r"m_requests\[([^\[\]\n]+)\]", r"m_requests.GetMutable(\1)", text)
    path.write_text(text)

bad = []
for path in paths:
    text = path.read_text()
    for token in ("m_requests.find(", "m_requests.begin()", "m_requests.end()",
                  "m_requests.clear()", "m_requests["):
        if token in text:
            bad.append(f"{path}:{token}")
if bad:
    raise SystemExit("implicit request-store use remains: " + ", ".join(bad))

doc = Path("docs/modules/execution-service.md")
text = doc.read_text()
needle = "Historical command lookup uses a binary search over the sorted command index."
replacement = (needle + " The coordinator's RequestRecordStore is composition-based: "
               "Find explicitly denotes the possible disk-backed lookup, while hot-set "
               "iteration is in-memory only and never hides index I/O behind a standard-container API.")
text = replace_one(text, needle, replacement, "execution request-store documentation")
doc.write_text(text)
