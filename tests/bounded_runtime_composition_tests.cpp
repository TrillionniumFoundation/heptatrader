#include "features/feature_graph.h"
#include "management/durable_rollout_store.h"
#include "simulator/multi_agent_allocation_scenario.h"
#include "strategy_runtime/strategy_runtime_control.h"

#include <cassert>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <string>
#include <vector>

namespace
{

std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

FeatureGraphNode Input(const std::string& id, const std::string& source)
{
    FeatureGraphNode node;
    node.id = id;
    node.kind = FeatureGraphNodeKind::Input;
    node.sourceName = source;
    return node;
}

void TestStrategyRuntime()
{
    StrategyRuntimeControl control(2);
    StrategyArtifactDescriptor descriptor;
    descriptor.moduleId = "hepta.strategy.alpha";
    descriptor.version = "1.0.0";
    descriptor.artifactDigest = Digest('a');
    descriptor.configDigest = Digest('b');
    descriptor.modelDigest = Digest('c');
    descriptor.budget.maxThreads = 2;
    descriptor.budget.maxFileDescriptors = 32;
    descriptor.budget.maxMemoryBytes = 64ULL * 1024ULL * 1024ULL;
    descriptor.budget.maxCheckpointBytes = 1024ULL * 1024ULL;

    const StrategyRuntimeControlResult admitted = control.Admit(descriptor, 100);
    assert(admitted.accepted && admitted.snapshot.generation == 1);
    assert(control.Start(descriptor.moduleId, 1, Digest('f'), 110).reasonCode ==
           "STRATEGY_ARTIFACT_DIGEST_MISMATCH");

    const StrategyRuntimeControlResult running =
        control.Start(descriptor.moduleId, 1, descriptor.artifactDigest, 110);
    assert(running.accepted && running.snapshot.generation == 2);
    assert(control.Checkpoint(descriptor.moduleId, 2, 1, Digest('d'),
                              descriptor.budget.maxCheckpointBytes + 1,
                              120).reasonCode ==
           "STRATEGY_CHECKPOINT_BUDGET_EXCEEDED");

    const StrategyRuntimeControlResult checkpoint =
        control.Checkpoint(descriptor.moduleId, 2, 1, Digest('d'), 1024, 120);
    assert(checkpoint.accepted && checkpoint.snapshot.generation == 3);
    const StrategyRuntimeControlResult duplicate =
        control.Checkpoint(descriptor.moduleId, 3, 1, Digest('d'), 1024, 121);
    assert(duplicate.accepted && duplicate.duplicate);
    assert(control.Checkpoint(descriptor.moduleId, 3, 1, Digest('e'), 1024,
                              121).reasonCode ==
           "STRATEGY_CHECKPOINT_SEQUENCE_CONFLICT");

    const StrategyRuntimeControlResult quarantined =
        control.Quarantine(descriptor.moduleId, 3, "HEALTH_DRIFT", 130);
    assert(quarantined.accepted && quarantined.snapshot.generation == 4);

    StrategyArtifactDescriptor replacement = descriptor;
    replacement.version = "1.1.0";
    replacement.artifactDigest = Digest('e');
    const StrategyRuntimeControlResult replaced =
        control.Replace(replacement, 4, 140);
    assert(replaced.accepted && replaced.snapshot.generation == 5);
    assert(replaced.snapshot.checkpointSequence == 0);
    assert(control.Start(replacement.moduleId, 5,
                         replacement.artifactDigest, 150).accepted);
}

void TestDurableRollout(const std::filesystem::path& directory)
{
    const std::filesystem::path path = directory / "rollout.store";
    DurableRolloutStore store(path, 4, 65536);
    assert(store.Load().accepted);

    DurableRolloutRecord record;
    record.moduleId = "hepta.strategy.alpha";
    record.version = "1.0.0";
    record.artifactDigest = Digest('a');
    record.configDigest = Digest('b');
    record.modelDigest = Digest('c');
    record.desiredState = "active";
    record.generation = 1;
    record.updatedAtMs = 100;
    assert(store.Put(record).accepted);
    const DurableRolloutResult duplicate = store.Put(record);
    assert(duplicate.accepted && duplicate.duplicate);

    DurableRolloutStore recovered(path, 4, 65536);
    assert(recovered.Load().accepted);
    DurableRolloutRecord loaded;
    assert(recovered.Get(record.moduleId, loaded));
    assert(loaded.artifactDigest == record.artifactDigest);

    DurableRolloutRecord stale = record;
    stale.desiredState = "stopped";
    assert(recovered.Put(stale).reasonCode == "ROLLOUT_GENERATION_STALE");

    DurableRolloutRecord next = stale;
    next.generation = 2;
    next.updatedAtMs = 110;
    assert(recovered.Put(next).accepted);

    std::vector<ObservedRolloutState> observed;
    observed.push_back({record.moduleId, record.artifactDigest,
                        record.configDigest, "active"});
    const std::vector<RolloutReconciliationAction> actions =
        recovered.Reconcile(observed);
    assert(actions.size() == 1);
    assert(actions[0].action == "transition");

    {
        std::ofstream corrupt(path, std::ios::binary | std::ios::app);
        assert(corrupt.good());
        corrupt << "corruption";
    }
    assert(recovered.Load().reasonCode == "ROLLOUT_STORE_FORMAT_INVALID");
}

void TestScenario()
{
    MultiAgentAllocationScenario scenario(1, 4);
    MultiAgentScenarioEvent later;
    later.atMs = 20;
    later.sequence = 2;
    later.targetModule = "hepta.strategy.beta";
    later.eventType = "proposal";
    later.payloadDigest = Digest('b');
    MultiAgentScenarioEvent earlier = later;
    earlier.atMs = 10;
    earlier.sequence = 1;
    earlier.targetModule = "hepta.strategy.alpha";
    earlier.payloadDigest = Digest('a');

    assert(scenario.Add(later).accepted);
    assert(scenario.Add(earlier).accepted);
    const MultiAgentScenarioResult sealed = scenario.Seal();
    assert(sealed.accepted && !sealed.scenarioDigest.empty());

    const MultiAgentScenarioResult first = scenario.AdvanceTo(15);
    assert(first.accepted && first.emitted.size() == 1);
    assert(first.emitted[0].targetModule == "hepta.strategy.alpha");
    const MultiAgentScenarioSnapshot snapshot = first.snapshot;

    const MultiAgentScenarioResult second = scenario.AdvanceTo(30);
    assert(second.accepted && second.emitted.size() == 1);
    assert(second.emitted[0].targetModule == "hepta.strategy.beta");

    assert(scenario.Restore(snapshot).accepted);
    const MultiAgentScenarioResult replay = scenario.AdvanceTo(30);
    assert(replay.accepted && replay.emitted.size() == 1);
    assert(replay.emitted[0].payloadDigest == second.emitted[0].payloadDigest);

    MultiAgentAllocationScenario duplicateOrder(1, 4);
    MultiAgentScenarioEvent collision = earlier;
    collision.targetModule = "hepta.strategy.gamma";
    assert(duplicateOrder.Add(earlier).accepted);
    assert(duplicateOrder.Add(collision).accepted);
    assert(duplicateOrder.Seal().reasonCode ==
           "SCENARIO_ORDER_KEY_DUPLICATE");
}

void TestFeatureGraph()
{
    BoundedFeatureGraph graph(8, 8);
    assert(graph.AddNode(Input("x", "x")).accepted);
    assert(graph.AddNode(Input("y", "y")).accepted);

    FeatureGraphNode add;
    add.id = "sum";
    add.kind = FeatureGraphNodeKind::Add;
    add.inputs = {"x", "y"};
    assert(graph.AddNode(add).accepted);

    FeatureGraphNode mean;
    mean.id = "mean";
    mean.kind = FeatureGraphNodeKind::RollingMean;
    mean.inputs = {"sum"};
    mean.window = 3;
    assert(graph.AddNode(mean).accepted);
    assert(graph.Seal().accepted);

    const FeatureGraphResult first = graph.Evaluate({{"x", 10}, {"y", 2}}, 1);
    assert(first.accepted && first.reasonCode == "FEATURE_GRAPH_WARMING");
    assert(!first.values.at("mean").ready);
    const FeatureGraphResult second = graph.Evaluate({{"x", 20}, {"y", 4}}, 2);
    assert(second.accepted && !second.values.at("mean").ready);
    const FeatureGraphResult third = graph.Evaluate({{"x", 30}, {"y", 6}}, 3);
    assert(third.accepted && third.values.at("mean").ready);
    assert(third.values.at("mean").value == 24);
    assert(graph.Evaluate({{"x", 1}, {"y", 1}}, 3).reasonCode ==
           "FEATURE_SEQUENCE_STALE");

    BoundedFeatureGraph cycle(4, 4);
    FeatureGraphNode a;
    a.id = "a";
    a.kind = FeatureGraphNodeKind::Add;
    a.inputs = {"b", "b"};
    FeatureGraphNode b = a;
    b.id = "b";
    b.inputs = {"a", "a"};
    assert(cycle.AddNode(a).accepted);
    assert(cycle.AddNode(b).accepted);
    assert(cycle.Seal().reasonCode == "FEATURE_GRAPH_CYCLE");

    BoundedFeatureGraph overflow(4, 4);
    assert(overflow.AddNode(Input("left", "left")).accepted);
    assert(overflow.AddNode(Input("right", "right")).accepted);
    FeatureGraphNode sum;
    sum.id = "sum";
    sum.kind = FeatureGraphNodeKind::Add;
    sum.inputs = {"left", "right"};
    assert(overflow.AddNode(sum).accepted);
    assert(overflow.Seal().accepted);
    assert(overflow.Evaluate(
        {{"left", std::numeric_limits<std::int64_t>::max()}, {"right", 1}},
        1).reasonCode == "FEATURE_ARITHMETIC_OVERFLOW");
}

} // namespace

int main()
{
    const auto nonce = std::chrono::steady_clock::now()
                           .time_since_epoch()
                           .count();
    const std::filesystem::path directory =
        std::filesystem::temp_directory_path() /
        ("hepta-bounded-runtime-" + std::to_string(nonce));
    std::filesystem::create_directories(directory);

    TestStrategyRuntime();
    TestDurableRollout(directory);
    TestScenario();
    TestFeatureGraph();

    std::error_code error;
    std::filesystem::remove_all(directory, error);
    assert(!error);
    return 0;
}
