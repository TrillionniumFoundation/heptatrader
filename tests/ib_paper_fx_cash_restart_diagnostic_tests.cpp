#include "execution/ib_paper_fx_cash_restart_catchup.h"

#include <cassert>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace {
using Code = IbPaperFxCashRestartCatchUpCode;

struct Fixture
{
    IbPaperFxCashRestartCatchUpContext context;
    std::map<std::string, IbPaperFxCashBaseline> baselines;
    OmsJournalEvent accepted, sent, execution;

    Fixture()
    {
        context.toolSource = "agent.tool:synthetic";
        context.brokerSource = "agent:synthetic";
        context.account = "DU-SYNTHETIC-PRIVATE";
        context.executionDomain = "PAPER";
        context.maxGrossPosition = 50000;
        context.brokerClientId = 701;
        IbPaperFxCashBaseline baseline;
        baseline.account = context.account;
        baseline.instrument = "EUR.USD";
        baseline.currency = "EUR";
        baseline.baselineCashBalance = 100000;
        baseline.observedCashBalance = 100000;
        baseline.campaignExecutionDelta = 0;
        baseline.observedAtMs = 1000;
        baseline.proof = "sha256:" + std::string(64, 'a');
        baselines[baseline.instrument] = baseline;
        sent.eventType = "place_sent";
        sent.tsMs = 2000;
        sent.orderId = 79123;
        sent.source = context.toolSource;
        sent.traceId = "synthetic-private-session";
        sent.strategy = "agent-native";
        sent.account = context.account;
        sent.executionDomain = context.executionDomain;
        sent.instrument = baseline.instrument;
        sent.side = "BUY";
        sent.reqId = "synthetic-private-command";
        sent.venueCorrelationId = "synthetic-private-correlation";
        sent.qty = 25000;
        sent.price = 1.1;
        accepted = sent;
        accepted.eventType = "broker_order_accepted";
        accepted.source = context.brokerSource;
        accepted.tsMs = 1999;
        accepted.brokerServiceEpoch = "synthetic-private-service";
        accepted.brokerConnectionEpoch = 2;
        accepted.brokerClientId = context.brokerClientId;
        execution = accepted;
        execution.eventType = "broker_execution";
        execution.tsMs = 2100;
        execution.side = "BOT";
        execution.brokerExecutionId = "synthetic-private-execution";
    }

    std::vector<OmsJournalEvent> events() const { return {accepted, sent, execution}; }
};

void Unchanged(const std::map<std::string, IbPaperFxCashBaseline>& actual,
               const std::map<std::string, IbPaperFxCashBaseline>& original)
{
    assert(actual.size() == original.size());
    for (const auto& item : original)
    {
        const auto& a = actual.at(item.first);
        const auto& b = item.second;
        assert(a.account == b.account && a.instrument == b.instrument && a.currency == b.currency);
        assert(a.baselineCashBalance == b.baselineCashBalance);
        assert(a.observedCashBalance == b.observedCashBalance);
        assert(a.campaignExecutionDelta == b.campaignExecutionDelta);
        assert(a.observedAtMs == b.observedAtMs && a.proof == b.proof);
    }
}

IbPaperFxCashRestartCatchUpDiagnostic Reject(
    const Fixture& fixture, const std::vector<OmsJournalEvent>& events, Code code,
    std::uint32_t mask = 0, bool economic = false)
{
    auto legacy = fixture.baselines;
    auto detailed = fixture.baselines;
    std::string oldReason, reason;
    IbPaperFxCashRestartCatchUpDiagnostic diagnostic;
    assert(!ApplyIbPaperFxCashRestartCatchUp(events, fixture.context, legacy, oldReason));
    assert(!ApplyIbPaperFxCashRestartCatchUp(events, fixture.context, detailed, reason, &diagnostic));
    assert(reason == "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID" && oldReason == reason);
    assert(diagnostic.code == code && diagnostic.ignoredExecutionMask == mask);
    assert(diagnostic.economicEvidenceObserved == economic);
    Unchanged(legacy, fixture.baselines);
    Unchanged(detailed, fixture.baselines);
    const std::string message = FormatIbPaperFxCashRestartCatchUpDiagnostic(diagnostic);
    for (const std::string& secret : {fixture.context.account, fixture.sent.traceId,
         fixture.sent.reqId, fixture.sent.venueCorrelationId, fixture.sent.instrument,
         fixture.execution.brokerExecutionId, std::string("79123")})
        if (!secret.empty()) assert(message.find(secret) == std::string::npos);
    return diagnostic;
}

void DirectFailureReasonsAndAtomicBaseline()
{
    Fixture f;
    f.execution.venueCorrelationId = "other";
    Reject(f, f.events(), Code::ExecutionCorrelationMismatch);
    f = Fixture(); f.execution.side = "SLD";
    Reject(f, f.events(), Code::ExecutionSideMismatch, 0, true);
    f = Fixture(); f.accepted.instrument = f.sent.instrument = f.execution.instrument = "GBP.USD";
    Reject(f, f.events(), Code::ExecutionBaselineMissing, 0, true);
    f = Fixture(); f.context.maxGrossPosition = 10000;
    Reject(f, f.events(), Code::ExecutionGrossLimit, 0, true);
    const double maximum = std::numeric_limits<double>::max();
    f = Fixture(); f.context.maxGrossPosition = maximum;
    f.sent.qty = f.accepted.qty = f.execution.qty = maximum;
    f.baselines.at("EUR.USD").baselineCashBalance = -maximum;
    f.baselines.at("EUR.USD").campaignExecutionDelta = maximum;
    f.baselines.at("EUR.USD").observedCashBalance = 0;
    Reject(f, f.events(), Code::ExecutionDeltaNonfinite, 0, true);
    f.baselines.at("EUR.USD").baselineCashBalance = maximum;
    f.baselines.at("EUR.USD").campaignExecutionDelta = 0;
    f.baselines.at("EUR.USD").observedCashBalance = maximum;
    Reject(f, f.events(), Code::ExecutionBalanceNonfinite, 0, true);
    f = Fixture();
    Reject(f, {f.accepted, f.sent}, Code::UnresolvedAcceptedOrder);
    f.execution.qty = 10000;
    Reject(f, f.events(), Code::UnresolvedAcceptedOrder, 0, true);
}

void EveryIgnoredExecutionPredicateRemainsNonAuthoritative()
{
    const std::uint32_t masks[] = {FxCatchUpServiceEpoch, FxCatchUpConnectionEpoch,
        FxCatchUpSession, FxCatchUpAccount, FxCatchUpStrategy, FxCatchUpDomain,
        FxCatchUpInstrument, FxCatchUpExecutionIdMissing, FxCatchUpQuantityNonfinite,
        FxCatchUpQuantityNonpositive, FxCatchUpQuantityExceedsSubmitted,
        FxCatchUpPriceNonfinite, FxCatchUpPriceNonpositive};
    for (unsigned variant = 0; variant < 13; ++variant)
    {
        Fixture f;
        switch (variant)
        {
        case 0: f.execution.brokerServiceEpoch = "other"; break;
        case 1: ++f.execution.brokerConnectionEpoch; break;
        case 2: f.execution.traceId = "other"; break;
        case 3: f.execution.account = "other"; break;
        case 4: f.execution.strategy = "other"; break;
        case 5: f.execution.executionDomain = "other"; break;
        case 6: f.execution.instrument = "other"; break;
        case 7: f.execution.brokerExecutionId.clear(); break;
        case 8: f.execution.qty = std::numeric_limits<double>::quiet_NaN(); break;
        case 9: f.execution.qty = 0; break;
        case 10: f.execution.qty += 1; break;
        case 11: f.execution.price = std::numeric_limits<double>::quiet_NaN(); break;
        case 12: f.execution.price = 0; break;
        }
        Reject(f, f.events(), Code::UnresolvedAcceptedOrder, masks[variant]);
    }
    Fixture f;
    auto wrong = f.execution;
    wrong.account = "other"; wrong.brokerServiceEpoch = "other";
    auto partial = f.execution; partial.qty = 10000;
    Reject(f, {f.accepted, f.sent, wrong, partial}, Code::UnresolvedAcceptedOrder,
           FxCatchUpAccount | FxCatchUpServiceEpoch, true);
    wrong.orderId += 1; // An unrelated order's mismatch is not this order's evidence.
    Reject(f, {f.accepted, f.sent, wrong}, Code::UnresolvedAcceptedOrder);
}

void SuccessAndUnrelatedErrorsClearDiagnosticState()
{
    Fixture f;
    auto events = f.events();
    auto wrong = f.execution; wrong.brokerServiceEpoch = "other";
    events.insert(events.begin() + 2, wrong);
    IbPaperFxCashRestartCatchUpDiagnostic diagnostic;
    diagnostic.code = Code::ExecutionGrossLimit;
    diagnostic.ignoredExecutionMask = 8191;
    diagnostic.economicEvidenceObserved = true;
    std::string reason;
    assert(ApplyIbPaperFxCashRestartCatchUp(events, f.context, f.baselines, reason, &diagnostic));
    assert(reason.empty());
    assert(f.baselines.at("EUR.USD").campaignExecutionDelta == 25000);
    assert(f.baselines.at("EUR.USD").observedCashBalance == 125000);
    assert(diagnostic.code == Code::None && diagnostic.ignoredExecutionMask == 0);
    assert(!diagnostic.economicEvidenceObserved);
    f.context.account.clear();
    diagnostic.code = Code::ExecutionGrossLimit;
    assert(!ApplyIbPaperFxCashRestartCatchUp(events, f.context, f.baselines, reason, &diagnostic));
    assert(reason == "IB_FX_CASH_RESTART_CATCHUP_BASELINE_INVALID");
    assert(diagnostic.code == Code::None && diagnostic.ignoredExecutionMask == 0);
    diagnostic.code = Code::AdvancedBaselineInvalid;
    assert(FormatIbPaperFxCashRestartCatchUpDiagnostic(diagnostic) ==
           "detail=ADVANCED_BASELINE_INVALID ignored_execution_mask=0 economic_evidence_observed=0");
}
} // namespace

int main()
{
    DirectFailureReasonsAndAtomicBaseline();
    EveryIgnoredExecutionPredicateRemainsNonAuthoritative();
    SuccessAndUnrelatedErrorsClearDiagnosticState();
    std::cout << "FX CASH restart catch-up diagnostic controls passed\n";
}
