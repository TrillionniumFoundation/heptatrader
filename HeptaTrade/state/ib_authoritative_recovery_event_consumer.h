#pragma once

#include "../adapter_ib/ib_api_wrapper.h"

#include "ib_authoritative_account_position_consumer.h"
#include "ib_authoritative_open_order_consumer.h"
#include "ib_authoritative_quote_subscription_set.h"
#include "ib_authoritative_recovery_coordinator.h"

enum class IBAuthoritativeRecoveryEventCompletionKind
{
    None = 0,
    AccountSummary,
    Positions,
    OpenOrders
};

struct IBAuthoritativeRecoveryEventCompletion
{
    bool handled = false;
    bool hadActiveGeneration = false;
    bool recoveryWasPending = false;
    bool snapshotAccepted = false;
    std::uint64_t generation = 0;
    std::string reasonCode;
    IBAuthoritativeRecoveryEventCompletionKind kind =
        IBAuthoritativeRecoveryEventCompletionKind::None;
    IBAuthoritativeRecoveryCompletionResult recovery;
    IBAuthoritativeAccountCompletion account;
    IBAuthoritativePositionCompletion positions;
    IBAuthoritativeOpenOrderCompletion openOrders;
};

struct IBAuthoritativeRecoveryQuoteEventResult
{
    IBAuthoritativeQuoteConsumeResult quote;
    bool recoveryCompletionAttempted = false;
    IBAuthoritativeRecoveryCompletionResult recovery;
};

struct IBAuthoritativeRecoveryControlAction
{
    bool handled = false;
    bool overflow = false;
    bool reconnectEpoch = false;
    bool forceDisconnect = false;
    int errorCode = 0;
    std::uint64_t overflowGeneration = 0;
    std::string recoveryReason;
};

class IBAuthoritativeRecoveryEventConsumer
{
public:
    IBAuthoritativeRecoveryEventConsumer(
        IBAuthoritativeRecoveryCoordinator& recovery,
        IBAuthoritativeAccountPositionConsumer& accountPositions,
        IBAuthoritativeOpenOrderConsumer& openOrders,
        IBAuthoritativeQuoteSubscriptionSet& quotes);

    IBAuthoritativeRecoveryEventCompletion ConsumeCompletion(
        const IBEvent& event,
        std::uint64_t observedAtMs);
    IBAuthoritativeRecoveryQuoteEventResult ConsumeQuote(
        const IBEvent& event,
        std::uint64_t observedAtMs);
    static IBAuthoritativeRecoveryControlAction ClassifyControlEvent(const IBEvent& event);

private:
    IBAuthoritativeRecoveryCoordinator& m_recovery;
    IBAuthoritativeAccountPositionConsumer& m_accountPositions;
    IBAuthoritativeOpenOrderConsumer& m_openOrders;
    IBAuthoritativeQuoteSubscriptionSet& m_quotes;
};
