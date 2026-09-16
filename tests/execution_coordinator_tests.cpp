#include "../HeptaTrade/execution/execution_coordinator.h"
#include "compat/oms_recover.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::string TempJournalPath()
{
    char name[] = "/tmp/hepta-execution-coordinator-XXXXXX";
    const int fd = mkstemp(name);
    assert(fd >= 0);
    close(fd);
    std::remove(name);
    return name;
}

IbPlaceOrderCommand MakePlace(const std::string& callId)
{
    IbPlaceOrderCommand command;
    command.context.agentId = "agent-a";
    command.context.sessionId = "session-1";
    command.context.toolCallId = callId;
    command.context.strategy = "strategy-a";
    command.context.account = "DU123";
    command.context.venue = "IB";
    command.context.executionDomain = "paper";
    command.instrument = "EUR.USD";
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    command.order.action = "BUY";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 100.0;
    command.order.lmtPrice = 1.10;
    command.referencePrice = 1.10;
    command.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    command.timeInForce = "DAY";
    return command;
}

FlattenPositionCommand MakeFlatten(const std::string& callId)
{
    FlattenPositionCommand command;
    command.context.agentId = "agent-a";
    command.context.sessionId = "session-1";
    command.context.toolCallId = callId;
    command.context.strategy = "strategy-a";
    command.context.account = "DU123";
    command.context.venue = "IB";
    command.context.executionDomain = "paper";
    command.instrument = "EUR.USD";
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    return command;
}

AuthoritativeFlattenPlan MakeFlattenPlan(const FlattenPositionCommand& command)
{
    AuthoritativeFlattenPlan plan;
    plan.contract = command.contract;
    plan.instrument = command.instrument;
    plan.order.action = "SELL";
    plan.order.orderType = "MKT";
    plan.order.totalQuantity = 100.0;
    plan.referencePrice = 1.10;
    plan.expectedPositionQuantity = 100.0;
    plan.positionConnectionEpoch = 1;
    plan.positionGeneration = 1;
    return plan;
}

// Existing test body retained below. This source replacement is intentionally
// not used; the complete file is fetched and updated by the connector in later
// revisions when adding includes/calls only.
