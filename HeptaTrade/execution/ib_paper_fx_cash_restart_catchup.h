#pragma once

#include "ib_paper_execution_runtime_config.h"
#include "../oms_journal.h"

#include <map>
#include <cstdint>
#include <string>
#include <vector>

struct IbPaperFxCashRestartCatchUpContext
{
    std::string toolSource;
    std::string brokerSource;
    std::string account;
    std::string executionDomain;
    double maxGrossPosition = 0.0;
    int brokerClientId = -1; // required for zero-fill terminal evidence
};

// Diagnostics contain fixed categories only, never broker or account identifiers,
// timestamps, quantities, prices, or raw journal fields. They grant no authority.
enum class IbPaperFxCashRestartCatchUpCode
{
    None,
    ExecutionCorrelationMismatch,
    ExecutionSideMismatch,
    ExecutionBaselineMissing,
    ExecutionDeltaNonfinite,
    ExecutionGrossLimit,
    ExecutionBalanceNonfinite,
    UnresolvedAcceptedOrder,
    AdvancedBaselineInvalid
};

enum IbPaperFxCashIgnoredExecution : std::uint32_t
{
    FxCatchUpServiceEpoch = 1u << 0,
    FxCatchUpConnectionEpoch = 1u << 1,
    FxCatchUpSession = 1u << 2,
    FxCatchUpAccount = 1u << 3,
    FxCatchUpStrategy = 1u << 4,
    FxCatchUpDomain = 1u << 5,
    FxCatchUpInstrument = 1u << 6,
    FxCatchUpExecutionIdMissing = 1u << 7,
    FxCatchUpQuantityNonfinite = 1u << 8,
    FxCatchUpQuantityNonpositive = 1u << 9,
    FxCatchUpQuantityExceedsSubmitted = 1u << 10,
    FxCatchUpPriceNonfinite = 1u << 11,
    FxCatchUpPriceNonpositive = 1u << 12
};

struct IbPaperFxCashRestartCatchUpDiagnostic
{
    IbPaperFxCashRestartCatchUpCode code = IbPaperFxCashRestartCatchUpCode::None;
    std::uint32_t ignoredExecutionMask = 0;
    bool economicEvidenceObserved = false;
};

std::string FormatIbPaperFxCashRestartCatchUpDiagnostic(
    const IbPaperFxCashRestartCatchUpDiagnostic& diagnostic);

bool ApplyIbPaperFxCashRestartCatchUp(
    const std::vector<OmsJournalEvent>& events,
    const IbPaperFxCashRestartCatchUpContext& context,
    std::map<std::string, IbPaperFxCashBaseline>& baselines,
    std::string& reason);

// The legacy overload and top-level reason strings remain unchanged.
bool ApplyIbPaperFxCashRestartCatchUp(
    const std::vector<OmsJournalEvent>& events,
    const IbPaperFxCashRestartCatchUpContext& context,
    std::map<std::string, IbPaperFxCashBaseline>& baselines,
    std::string& reason,
    IbPaperFxCashRestartCatchUpDiagnostic* diagnostic);
