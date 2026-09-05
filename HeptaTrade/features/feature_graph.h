#pragma once

#include <algorithm>
#include <cstdint>
#include <deque>
#include <iomanip>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

enum class FeatureGraphNodeKind
{
    Input,
    Add,
    Subtract,
    MultiplyScaled,
    DivideScaled,
    RollingMean
};

struct FeatureGraphNode
{
    std::string id;
    FeatureGraphNodeKind kind = FeatureGraphNodeKind::Input;
    std::vector<std::string> inputs;
    std::string sourceName;
    std::int64_t scale = 1000000;
    std::size_t window = 0;
};

struct FeatureGraphValue
{
    std::int64_t value = 0;
    bool ready = false;
};

struct FeatureGraphResult
{
    bool accepted = false;
    std::string reasonCode;
    std::uint64_t sequence = 0;
    std::string digest;
    std::map<std::string, FeatureGraphValue> values;
};

// Bounded deterministic DAG evaluator. It provides a fixed node catalog and
// transactional rolling state; it intentionally provides neither arbitrary
// plugin execution nor persistent/offline feature-store parity.
class BoundedFeatureGraph
{
public:
    explicit BoundedFeatureGraph(std::size_t maximumNodes = 256,
                                 std::size_t maximumHistoryPerNode = 4096)
        : m_maximumNodes(maximumNodes),
          m_maximumHistoryPerNode(maximumHistoryPerNode)
    {
    }

    static const char* Version() noexcept
    {
        return "hepta.bounded-feature-graph.v1";
    }

    FeatureGraphResult AddNode(const FeatureGraphNode& node)
    {
        if (m_sealed) return Reject("FEATURE_GRAPH_ALREADY_SEALED");
        if (!CanonicalId(node.id, 128))
            return Reject("FEATURE_NODE_ID_INVALID");
        if (m_nodes.find(node.id) != m_nodes.end())
            return Reject("FEATURE_NODE_DUPLICATE");
        if (m_nodes.size() >= m_maximumNodes)
            return Reject("FEATURE_NODE_LIMIT");
        if (!ValidShape(node)) return Reject("FEATURE_NODE_SHAPE_INVALID");
        m_nodes.emplace(node.id, node);
        return Accept("FEATURE_NODE_ADDED");
    }

    FeatureGraphResult Seal()
    {
        if (m_sealed)
        {
            FeatureGraphResult result = Accept("FEATURE_GRAPH_SEAL_DUPLICATE");
            result.digest = m_definitionDigest;
            return result;
        }
        if (m_nodes.empty()) return Reject("FEATURE_GRAPH_EMPTY");

        std::map<std::string, std::size_t> indegree;
        std::map<std::string, std::vector<std::string>> dependents;
        for (const auto& entry : m_nodes)
        {
            indegree[entry.first] = entry.second.inputs.size();
            for (const std::string& dependency : entry.second.inputs)
            {
                if (m_nodes.find(dependency) == m_nodes.end())
                    return Reject("FEATURE_DEPENDENCY_MISSING");
                dependents[dependency].push_back(entry.first);
            }
        }
        std::set<std::string> ready;
        for (const auto& entry : indegree)
            if (entry.second == 0) ready.insert(entry.first);

        std::vector<std::string> order;
        order.reserve(m_nodes.size());
        while (!ready.empty())
        {
            const std::string current = *ready.begin();
            ready.erase(ready.begin());
            order.push_back(current);
            std::vector<std::string>& children = dependents[current];
            std::sort(children.begin(), children.end());
            for (const std::string& child : children)
            {
                std::size_t& count = indegree[child];
                if (count == 0) return Reject("FEATURE_GRAPH_INTERNAL_INVALID");
                --count;
                if (count == 0) ready.insert(child);
            }
        }
        if (order.size() != m_nodes.size())
            return Reject("FEATURE_GRAPH_CYCLE");

        m_order.swap(order);
        m_definitionDigest = DefinitionDigest();
        m_sealed = true;
        FeatureGraphResult result = Accept("FEATURE_GRAPH_SEALED");
        result.digest = m_definitionDigest;
        return result;
    }

    FeatureGraphResult Evaluate(
        const std::map<std::string, std::int64_t>& inputs,
        std::uint64_t sequence)
    {
        if (!m_sealed) return Reject("FEATURE_GRAPH_NOT_SEALED");
        if (sequence == 0 || sequence <= m_lastSequence)
            return Reject("FEATURE_SEQUENCE_STALE");

        std::map<std::string, std::deque<std::int64_t>> histories = m_histories;
        FeatureGraphResult result;
        result.sequence = sequence;
        bool allReady = true;

        for (const std::string& id : m_order)
        {
            const FeatureGraphNode& node = m_nodes.at(id);
            FeatureGraphValue output;
            if (node.kind == FeatureGraphNodeKind::Input)
            {
                const auto found = inputs.find(node.sourceName);
                if (found == inputs.end())
                    return Reject("FEATURE_INPUT_MISSING", sequence);
                output.value = found->second;
                output.ready = true;
            }
            else
            {
                std::vector<FeatureGraphValue> dependencies;
                dependencies.reserve(node.inputs.size());
                bool dependencyReady = true;
                for (const std::string& dependency : node.inputs)
                {
                    const auto found = result.values.find(dependency);
                    if (found == result.values.end())
                        return Reject("FEATURE_EVALUATION_ORDER_INVALID", sequence);
                    dependencies.push_back(found->second);
                    dependencyReady = dependencyReady && found->second.ready;
                }

                if (node.kind == FeatureGraphNodeKind::RollingMean)
                {
                    if (!dependencyReady)
                    {
                        output.ready = false;
                    }
                    else
                    {
                        std::deque<std::int64_t>& history = histories[id];
                        history.push_back(dependencies[0].value);
                        while (history.size() > node.window) history.pop_front();
                        __int128 sum = 0;
                        for (std::int64_t value : history)
                            sum += static_cast<__int128>(value);
                        const __int128 average =
                            sum / static_cast<__int128>(history.size());
                        if (!Fits(average))
                            return Reject("FEATURE_ARITHMETIC_OVERFLOW", sequence);
                        output.value = static_cast<std::int64_t>(average);
                        output.ready = history.size() == node.window;
                    }
                }
                else if (!dependencyReady)
                {
                    output.ready = false;
                }
                else
                {
                    __int128 computed = 0;
                    const __int128 left =
                        static_cast<__int128>(dependencies[0].value);
                    const __int128 right =
                        static_cast<__int128>(dependencies[1].value);
                    switch (node.kind)
                    {
                    case FeatureGraphNodeKind::Add:
                        computed = left + right;
                        break;
                    case FeatureGraphNodeKind::Subtract:
                        computed = left - right;
                        break;
                    case FeatureGraphNodeKind::MultiplyScaled:
                        computed = (left * right) /
                            static_cast<__int128>(node.scale);
                        break;
                    case FeatureGraphNodeKind::DivideScaled:
                        if (right == 0)
                            return Reject("FEATURE_DIVIDE_BY_ZERO", sequence);
                        computed = (left * static_cast<__int128>(node.scale)) /
                            right;
                        break;
                    case FeatureGraphNodeKind::Input:
                    case FeatureGraphNodeKind::RollingMean:
                        return Reject("FEATURE_NODE_KIND_INVALID", sequence);
                    }
                    if (!Fits(computed))
                        return Reject("FEATURE_ARITHMETIC_OVERFLOW", sequence);
                    output.value = static_cast<std::int64_t>(computed);
                    output.ready = true;
                }
            }
            allReady = allReady && output.ready;
            result.values.emplace(id, output);
        }

        result.accepted = true;
        result.reasonCode =
            allReady ? "FEATURE_GRAPH_EVALUATED" : "FEATURE_GRAPH_WARMING";
        result.digest = EvaluationDigest(sequence, result.values);
        m_histories.swap(histories);
        m_lastSequence = sequence;
        return result;
    }

    std::uint64_t LastSequence() const noexcept { return m_lastSequence; }
    const std::string& DefinitionDigestValue() const noexcept
    {
        return m_definitionDigest;
    }

private:
    static bool CanonicalId(const std::string& value, std::size_t maximum)
    {
        if (value.empty() || value.size() > maximum) return false;
        for (unsigned char c : value)
        {
            const bool alnum = (c >= 'A' && c <= 'Z') ||
                (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
            if (!(alnum || c == '-' || c == '_' || c == '.' || c == ':'))
                return false;
        }
        return true;
    }

    bool ValidShape(const FeatureGraphNode& node) const
    {
        switch (node.kind)
        {
        case FeatureGraphNodeKind::Input:
            return node.inputs.empty() &&
                CanonicalId(node.sourceName, 128) && node.window == 0;
        case FeatureGraphNodeKind::Add:
        case FeatureGraphNodeKind::Subtract:
            return node.inputs.size() == 2 && node.sourceName.empty() &&
                node.window == 0;
        case FeatureGraphNodeKind::MultiplyScaled:
        case FeatureGraphNodeKind::DivideScaled:
            return node.inputs.size() == 2 && node.sourceName.empty() &&
                node.window == 0 && node.scale > 0;
        case FeatureGraphNodeKind::RollingMean:
            return node.inputs.size() == 1 && node.sourceName.empty() &&
                node.window > 0 && node.window <= m_maximumHistoryPerNode;
        }
        return false;
    }

    static bool Fits(__int128 value)
    {
        return value >=
                static_cast<__int128>(std::numeric_limits<std::int64_t>::min()) &&
            value <=
                static_cast<__int128>(std::numeric_limits<std::int64_t>::max());
    }

    static std::uint64_t Fnv1a(const std::string& value)
    {
        std::uint64_t hash = 1469598103934665603ULL;
        for (unsigned char c : value)
        {
            hash ^= static_cast<std::uint64_t>(c);
            hash *= 1099511628211ULL;
        }
        return hash;
    }

    static void Append(std::string& output, const std::string& value)
    {
        output.append(std::to_string(value.size()));
        output.push_back(':');
        output.append(value);
        output.push_back(';');
    }

    static std::string HashText(const std::string& canonical)
    {
        std::ostringstream output;
        output << "fnv1a64:" << std::hex << std::setfill('0')
               << std::setw(16) << Fnv1a(canonical);
        return output.str();
    }

    std::string DefinitionDigest() const
    {
        std::string canonical = Version();
        canonical.push_back(';');
        for (const std::string& id : m_order)
        {
            const FeatureGraphNode& node = m_nodes.at(id);
            Append(canonical, node.id);
            canonical.append(std::to_string(static_cast<int>(node.kind)));
            canonical.push_back(';');
            Append(canonical, node.sourceName);
            canonical.append(std::to_string(node.scale));
            canonical.push_back(';');
            canonical.append(std::to_string(node.window));
            canonical.push_back(';');
            for (const std::string& dependency : node.inputs)
                Append(canonical, dependency);
        }
        return HashText(canonical);
    }

    std::string EvaluationDigest(
        std::uint64_t sequence,
        const std::map<std::string, FeatureGraphValue>& values) const
    {
        std::string canonical = m_definitionDigest;
        canonical.push_back(';');
        canonical.append(std::to_string(sequence));
        canonical.push_back(';');
        for (const auto& entry : values)
        {
            Append(canonical, entry.first);
            canonical.append(std::to_string(entry.second.value));
            canonical.push_back(';');
            canonical.push_back(entry.second.ready ? '1' : '0');
            canonical.push_back(';');
        }
        return HashText(canonical);
    }

    static FeatureGraphResult Accept(const char* code)
    {
        FeatureGraphResult result;
        result.accepted = true;
        result.reasonCode = code;
        return result;
    }

    static FeatureGraphResult Reject(const char* code,
                                     std::uint64_t sequence = 0)
    {
        FeatureGraphResult result;
        result.reasonCode = code;
        result.sequence = sequence;
        return result;
    }

    std::size_t m_maximumNodes;
    std::size_t m_maximumHistoryPerNode;
    bool m_sealed = false;
    std::uint64_t m_lastSequence = 0;
    std::string m_definitionDigest;
    std::map<std::string, FeatureGraphNode> m_nodes;
    std::vector<std::string> m_order;
    std::map<std::string, std::deque<std::int64_t>> m_histories;
};
