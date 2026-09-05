#include "../HeptaTrade/management/module_lifecycle.h"

#include <cassert>
#include <string>

namespace
{
std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

ModuleArtifactIdentity Identity(const std::string& module,
                                const std::string& version,
                                char artifact)
{
    ModuleArtifactIdentity identity;
    identity.moduleId = module;
    identity.version = version;
    identity.artifactDigest = Digest(artifact);
    identity.configDigest = Digest('c');
    identity.modelDigest = Digest('d');
    return identity;
}

ModuleHealthEvidence Health(std::uint64_t observedAtMs, char evidence = 'e')
{
    ModuleHealthEvidence health;
    health.healthy = true;
    health.observedAtMs = observedAtMs;
    health.evidenceDigest = Digest(evidence);
    return health;
}

ModuleLifecycleSnapshot Activate(ModuleLifecycleRegistry& registry,
                                 const ModuleArtifactIdentity& identity)
{
    ModuleLifecycleResult registered = registry.Register(identity, 1000);
    assert(registered.accepted && registered.snapshot.generation == 1);
    ModuleLifecycleResult warming = registry.Transition(
        identity.moduleId, 1, ModuleLifecycleState::Warming,
        ModuleHealthEvidence(), 1100);
    assert(warming.accepted && warming.snapshot.generation == 2);
    ModuleLifecycleResult shadow = registry.Transition(
        identity.moduleId, 2, ModuleLifecycleState::Shadow,
        Health(1150), 1200);
    assert(shadow.accepted && shadow.snapshot.generation == 3);
    ModuleLifecycleResult active = registry.Transition(
        identity.moduleId, 3, ModuleLifecycleState::Active,
        Health(1250), 1300);
    assert(active.accepted && active.snapshot.generation == 4);
    return active.snapshot;
}

void TestActivationAndStaleFence()
{
    ModuleLifecycleRegistry registry;
    ModuleArtifactIdentity identity =
        Identity("hepta.strategy.alpha", "1.0.0", 'a');
    ModuleLifecycleResult registered = registry.Register(identity, 1000);
    assert(registered.accepted);
    assert(registry.Register(identity, 1001).reasonCode ==
           "MODULE_REGISTRATION_DUPLICATE");
    assert(registry.Transition(
        identity.moduleId, 1, ModuleLifecycleState::Active,
        Health(1000), 1100).reasonCode == "MODULE_TRANSITION_INVALID");
    assert(registry.Transition(
        identity.moduleId, 1, ModuleLifecycleState::Warming,
        ModuleHealthEvidence(), 1100).accepted);
    assert(registry.Transition(
        identity.moduleId, 1, ModuleLifecycleState::Shadow,
        Health(1100), 1200).reasonCode == "MODULE_GENERATION_STALE");
    assert(registry.Transition(
        identity.moduleId, 2, ModuleLifecycleState::Shadow,
        ModuleHealthEvidence(), 1200).reasonCode ==
        "MODULE_HEALTH_EVIDENCE_INVALID");
    assert(registry.Transition(
        identity.moduleId, 2, ModuleLifecycleState::Shadow,
        Health(1000), 40000).reasonCode ==
        "MODULE_HEALTH_EVIDENCE_INVALID");
}

void TestQuarantineAndActiveList()
{
    ModuleLifecycleRegistry registry;
    ModuleLifecycleSnapshot alpha = Activate(
        registry, Identity("hepta.strategy.alpha", "1.0.0", 'a'));
    ModuleLifecycleSnapshot beta = Activate(
        registry, Identity("hepta.strategy.beta", "1.0.0", 'b'));
    assert(registry.ListActive().size() == 2);
    ModuleLifecycleResult quarantined = registry.Quarantine(
        beta.identity.moduleId, beta.generation,
        "MODULE_HEALTH_FAILED", 1400);
    assert(quarantined.accepted);
    assert(quarantined.snapshot.state == ModuleLifecycleState::Quarantined);
    assert(registry.ListActive().size() == 1);
    assert(registry.Quarantine(
        alpha.identity.moduleId, alpha.generation,
        "bad reason", 1400).reasonCode ==
        "MODULE_QUARANTINE_REASON_INVALID");
}

void TestUpgradeRollback()
{
    ModuleLifecycleRegistry registry;
    ModuleLifecycleSnapshot active = Activate(
        registry, Identity("hepta.strategy.alpha", "1.0.0", 'a'));
    ModuleLifecycleResult staged = registry.StageUpgrade(
        Identity("hepta.strategy.alpha", "2.0.0", 'b'),
        active.generation, 1400);
    assert(staged.accepted);
    assert(staged.snapshot.state == ModuleLifecycleState::Warming);
    assert(staged.snapshot.identity.version == "2.0.0");
    assert(registry.StageUpgrade(
        Identity("hepta.strategy.alpha", "3.0.0", 'd'),
        active.generation, 1500).reasonCode == "MODULE_GENERATION_STALE");

    ModuleLifecycleResult shadow = registry.Transition(
        staged.snapshot.identity.moduleId, staged.snapshot.generation,
        ModuleLifecycleState::Shadow, Health(1450), 1500);
    assert(shadow.accepted);
    ModuleLifecycleResult quarantine = registry.Quarantine(
        shadow.snapshot.identity.moduleId, shadow.snapshot.generation,
        "MODULE_SHADOW_DIVERGED", 1600);
    assert(quarantine.accepted);
    ModuleLifecycleResult rollback = registry.Rollback(
        quarantine.snapshot.identity.moduleId,
        quarantine.snapshot.generation, Health(1650), 1700);
    assert(rollback.accepted);
    assert(rollback.snapshot.state == ModuleLifecycleState::Active);
    assert(rollback.snapshot.identity.version == "1.0.0");
    assert(rollback.snapshot.generation == quarantine.snapshot.generation + 1);
    assert(registry.Rollback(
        rollback.snapshot.identity.moduleId, rollback.snapshot.generation,
        Health(1750), 1800).reasonCode == "MODULE_ROLLBACK_UNAVAILABLE");
}

void TestDrainStopAndValidation()
{
    ModuleLifecycleRegistry registry;
    ModuleArtifactIdentity invalid =
        Identity("bad", "1.0.0", 'a');
    assert(registry.Register(invalid, 1000).reasonCode ==
           "MODULE_REGISTRATION_INVALID");
    ModuleLifecycleSnapshot active = Activate(
        registry, Identity("hepta.strategy.alpha", "1.0.0", 'a'));
    ModuleLifecycleResult draining = registry.Transition(
        active.identity.moduleId, active.generation,
        ModuleLifecycleState::Draining, ModuleHealthEvidence(), 1400);
    assert(draining.accepted);
    ModuleLifecycleResult stopped = registry.Transition(
        active.identity.moduleId, draining.snapshot.generation,
        ModuleLifecycleState::Stopped, ModuleHealthEvidence(), 1500);
    assert(stopped.accepted);
    assert(registry.ListActive().empty());
    assert(registry.Transition(
        active.identity.moduleId, stopped.snapshot.generation,
        ModuleLifecycleState::Warming, ModuleHealthEvidence(), 1400)
        .reasonCode == "MODULE_TIME_REGRESSION");
}
}

int main()
{
    TestActivationAndStaleFence();
    TestQuarantineAndActiveList();
    TestUpgradeRollback();
    TestDrainStopAndValidation();
    return 0;
}
