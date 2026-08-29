#include "target_position_intent.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <sstream>

namespace
{
bool PositiveFinite(double value)
{
    return std::isfinite(value) && value > 0.0;
}

bool NonNegativeFinite(double value)
{
    return std::isfinite(value) && value >= 0.0;
}

bool NearlyEqual(double left, double right)
{
    const double scale = std::max(1.0, std::max(std::fabs(left), std::fabs(right)));
    return std::fabs(left - right) <= scale * 1e-12;
}

void AppendField(std::string& out, const char* name, const std::string& value)
{
    out.append(name);
    out.push_back('=');
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back(';');
}

std::string CanonicalDouble(double value)
{
    static_assert(sizeof(double) == sizeof(std::uint64_t),
                  "unsupported double representation");
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << bits;
    return out.str();
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();

    std::ostringstream out;
    out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

bool Reject(const char* code, const char* detail,
            std::string& reasonCode, std::string& reasonDetail)
{
    reasonCode = code;
    reasonDetail = detail;
    return false;
}

bool ValidatePolicy(const TargetPositionIntentPolicy& policy,
                    std::string& reasonCode, std::string& detail)
{
    if (policy.version.empty() ||
        !PositiveFinite(policy.maxOrderQuantity) ||
        !PositiveFinite(policy.maxAbsoluteTargetPosition) ||
        !NonNegativeFinite(policy.maxSlippageBps) ||
        policy.maxSlippageBps > 1000.0 ||
        policy.maxIntentLifetimeMs <= 0 ||
        policy.maxIntentLifetimeMs > 3600000)
        return Reject("INTENT_POLICY_INVALID", "target-position policy is invalid",
                      reasonCode, detail);
    return true;
}

bool ValidateSnapshot(const TargetPositionDecisionSnapshot& snapshot,
                      std::string& reasonCode, std::string& detail)
{
    if (snapshot.agentId.empty() || snapshot.sessionId.empty() ||
        snapshot.account.empty() || snapshot.executionDomain.empty() ||
        snapshot.executionServiceEpoch.empty() || snapshot.instrument.empty() ||
        snapshot.fencingGeneration == 0 ||
        snapshot.collectionWatermark == 0 || snapshot.snapshotWatermark == 0)
        return Reject("INTENT_SNAPSHOT_INCOMPLETE",
                      "snapshot identity or generation is incomplete",
                      reasonCode, detail);
    if (snapshot.eventWatermark > snapshot.collectionWatermark ||
        snapshot.collectionWatermark > snapshot.snapshotWatermark)
        return Reject("INTENT_SNAPSHOT_INCONSISTENT",
                      "snapshot watermarks are not monotonic",
                      reasonCode, detail);
    if (snapshot.collectionStartedAtMs <= 0 ||
        snapshot.collectionCompletedAtMs < snapshot.collectionStartedAtMs ||
        snapshot.quoteObservedAtMs <= 0 ||
        snapshot.quoteObservedAtMs > snapshot.collectionCompletedAtMs)
        return Reject("INTENT_SNAPSHOT_INCONSISTENT",
                      "snapshot timestamps are invalid",
                      reasonCode, detail);
    if (!PositiveFinite(snapshot.bid) || !PositiveFinite(snapshot.ask) ||
        snapshot.ask < snapshot.bid || !std::isfinite(snapshot.currentPosition))
        return Reject("INTENT_MARKET_STATE_INVALID",
                      "authoritative quote or position is invalid",
                      reasonCode, detail);
    return true;
}
}

bool TargetPositionIntentContract::BuildPlan(
    const TargetPositionDecisionSnapshot& snapshot,
    const TargetPositionIntentRequest& request,
    const TargetPositionIntentPolicy& policy,
    std::int64_t nowMs,
    TargetPositionExecutionPlan& plan,
    std::string& reasonCode,
    std::string& detail)
{
    plan = TargetPositionExecutionPlan();
    if (!ValidatePolicy(policy, reasonCode, detail) ||
        !ValidateSnapshot(snapshot, reasonCode, detail))
        return false;
    if (nowMs <= 0 || request.expiresAtMs <= nowMs ||
        request.expiresAtMs - nowMs > policy.maxIntentLifetimeMs)
        return Reject("INTENT_EXPIRY_INVALID",
                      "intent expiry is past or exceeds the bounded lifetime",
                      reasonCode, detail);
    if (!std::isfinite(request.targetPosition) ||
        std::fabs(request.targetPosition) > policy.maxAbsoluteTargetPosition)
        return Reject("INTENT_TARGET_LIMIT",
                      "target position is non-finite or outside the session limit",
                      reasonCode, detail);
    if (!NonNegativeFinite(request.maxSlippageBps) ||
        request.maxSlippageBps > policy.maxSlippageBps)
        return Reject("INTENT_SLIPPAGE_LIMIT",
                      "max slippage is outside the session limit",
                      reasonCode, detail);

    const double delta = request.targetPosition - snapshot.currentPosition;
    plan.orderType = "LMT";
    plan.timeInForce = "DAY";
    if (NearlyEqual(delta, 0.0))
    {
        plan.noOp = true;
        plan.previewPermit = PermitDigest(snapshot, request, policy, plan);
        if (plan.previewPermit.empty())
            return Reject("INTENT_PERMIT_HASH_FAILED",
                          "preview binding digest could not be created",
                          reasonCode, detail);
        reasonCode = "INTENT_NO_CHANGE";
        detail.clear();
        return true;
    }

    plan.quantity = std::fabs(delta);
    if (!PositiveFinite(plan.quantity) ||
        plan.quantity > policy.maxOrderQuantity)
        return Reject("INTENT_ORDER_QUANTITY_LIMIT",
                      "derived order quantity exceeds the session limit",
                      reasonCode, detail);

    if (delta > 0.0)
    {
        plan.side = "BUY";
        plan.referencePrice = snapshot.ask;
        plan.limitPrice = snapshot.ask *
            (1.0 + request.maxSlippageBps / 10000.0);
    }
    else
    {
        plan.side = "SELL";
        plan.referencePrice = snapshot.bid;
        plan.limitPrice = snapshot.bid *
            (1.0 - request.maxSlippageBps / 10000.0);
    }
    if (!PositiveFinite(plan.referencePrice) ||
        !PositiveFinite(plan.limitPrice))
        return Reject("INTENT_LIMIT_PRICE_INVALID",
                      "derived authoritative price is invalid",
                      reasonCode, detail);

    plan.previewPermit = PermitDigest(snapshot, request, policy, plan);
    if (plan.previewPermit.empty())
        return Reject("INTENT_PERMIT_HASH_FAILED",
                      "preview binding digest could not be created",
                      reasonCode, detail);
    reasonCode = "INTENT_PLAN_READY";
    detail.clear();
    return true;
}

std::string TargetPositionIntentContract::PermitDigest(
    const TargetPositionDecisionSnapshot& snapshot,
    const TargetPositionIntentRequest& request,
    const TargetPositionIntentPolicy& policy,
    const TargetPositionExecutionPlan& plan)
{
    std::string canonical;
    AppendField(canonical, "contract", "hepta.target-position-preview.v1");
    AppendField(canonical, "policy_version", policy.version);
    AppendField(canonical, "agent_id", snapshot.agentId);
    AppendField(canonical, "session_id", snapshot.sessionId);
    AppendField(canonical, "account", snapshot.account);
    AppendField(canonical, "execution_domain", snapshot.executionDomain);
    AppendField(canonical, "execution_epoch", snapshot.executionServiceEpoch);
    AppendField(canonical, "fencing_generation",
                std::to_string(snapshot.fencingGeneration));
    AppendField(canonical, "collection_watermark",
                std::to_string(snapshot.collectionWatermark));
    AppendField(canonical, "event_watermark",
                std::to_string(snapshot.eventWatermark));
    AppendField(canonical, "snapshot_watermark",
                std::to_string(snapshot.snapshotWatermark));
    AppendField(canonical, "instrument", snapshot.instrument);
    AppendField(canonical, "quote_observed_at_ms",
                std::to_string(snapshot.quoteObservedAtMs));
    AppendField(canonical, "bid", CanonicalDouble(snapshot.bid));
    AppendField(canonical, "ask", CanonicalDouble(snapshot.ask));
    AppendField(canonical, "current_position",
                CanonicalDouble(snapshot.currentPosition));
    AppendField(canonical, "target_position",
                CanonicalDouble(request.targetPosition));
    AppendField(canonical, "max_slippage_bps",
                CanonicalDouble(request.maxSlippageBps));
    AppendField(canonical, "expires_at_ms",
                std::to_string(request.expiresAtMs));
    AppendField(canonical, "no_op", plan.noOp ? "1" : "0");
    AppendField(canonical, "side", plan.side);
    AppendField(canonical, "order_type", plan.orderType);
    AppendField(canonical, "tif", plan.timeInForce);
    AppendField(canonical, "quantity", CanonicalDouble(plan.quantity));
    AppendField(canonical, "reference_price",
                CanonicalDouble(plan.referencePrice));
    AppendField(canonical, "limit_price", CanonicalDouble(plan.limitPrice));
    return Sha256(canonical);
}

bool TargetPositionIntentContract::PermitMatches(
    const std::string& permit,
    const TargetPositionDecisionSnapshot& snapshot,
    const TargetPositionIntentRequest& request,
    const TargetPositionIntentPolicy& policy,
    const TargetPositionExecutionPlan& plan)
{
    return !permit.empty() && permit == PermitDigest(snapshot, request, policy, plan);
}
