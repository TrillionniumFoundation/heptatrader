#pragma once

#include "ib_paper_execution_runtime_config.h"
#include "../oms_journal.h"

#include <map>
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

// A classification, never a permit to connect, trade, checkpoint, or promote.
enum class IbPaperFxCashCatchUpDisposition
{
    InvalidEvidence = 0,
    Complete,
    AwaitOriginalOrderEvidence
};
struct IbPaperPendingOriginalOrder
{
    long orderId = -1;
    std::string commandId;
    std::string correlationId;
    std::string sessionId;
    std::string instrument;
    std::string side;
};
struct IbPaperFxCashCatchUpAssessment
{
    IbPaperFxCashCatchUpDisposition disposition =
        IbPaperFxCashCatchUpDisposition::InvalidEvidence;
    std::vector<IbPaperPendingOriginalOrder> pending;
    std::string reason;
};

// Does not modify baselines. The legacy Apply function retains its exact
// fail-closed boolean contract and error strings.
IbPaperFxCashCatchUpAssessment InspectIbPaperFxCashRestartCatchUp(
    const std::vector<OmsJournalEvent>& events,
    const IbPaperFxCashRestartCatchUpContext& context,
    const std::map<std::string, IbPaperFxCashBaseline>& baselines);

bool ApplyIbPaperFxCashRestartCatchUp(
    const std::vector<OmsJournalEvent>& events,
    const IbPaperFxCashRestartCatchUpContext& context,
    std::map<std::string, IbPaperFxCashBaseline>& baselines,
    std::string& reason);
