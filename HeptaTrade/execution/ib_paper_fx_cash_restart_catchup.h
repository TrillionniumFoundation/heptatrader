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
};

bool ApplyIbPaperFxCashRestartCatchUp(
    const std::vector<OmsJournalEvent>& events,
    const IbPaperFxCashRestartCatchUpContext& context,
    std::map<std::string, IbPaperFxCashBaseline>& baselines,
    std::string& reason);
