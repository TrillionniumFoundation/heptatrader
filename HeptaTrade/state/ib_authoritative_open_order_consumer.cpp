#include "ib_authoritative_open_order_consumer.h"

IBAuthoritativeOpenOrderConsumer::IBAuthoritativeOpenOrderConsumer(
    AuthoritativeTradingSnapshotStore& store,
    const std::string& configuredAccount)
    : m_store(store), m_projector(store), m_configuredAccount(configuredAccount)
{
}

bool IBAuthoritativeOpenOrderConsumer::ConfigureAccount(
    const std::string& configuredAccount)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_generation != 0) return false;
    m_configuredAccount = configuredAccount;
    return true;
}

void IBAuthoritativeOpenOrderConsumer::BeginRefresh(std::uint64_t generation)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_generation = generation;
    m_rejected = false;
    m_rejectReason.clear();
    m_orders.clear();
}

void IBAuthoritativeOpenOrderConsumer::AbortRefresh(std::uint64_t generation)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (generation == 0 || generation != m_generation) return;
    m_generation = 0;
    m_rejected = false;
    m_rejectReason.clear();
    m_orders.clear();
}

bool IBAuthoritativeOpenOrderConsumer::IsRefreshInFlight() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_generation != 0;
}

void IBAuthoritativeOpenOrderConsumer::ApplyProjectionLocked(
    long orderId,
    const IBAuthoritativeOrderProjectionResult& projection)
{
    if (m_generation == 0 || projection.status != IBAuthoritativeOrderProjectionStatus::Applied)
        return;
    if (projection.hasOrder)
        m_orders[projection.order.orderId] = projection.order;
    else
        m_orders.erase(orderId);
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOpenOrderConsumer::ProjectPlaced(
    const IbPlaceOrderCommand& command,
    long orderId,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const IBAuthoritativeOrderProjectionResult projection =
        m_projector.ProjectPlaced(command, orderId, observedAtMs);
    ApplyProjectionLocked(orderId, projection);
    return projection;
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOpenOrderConsumer::ProjectCancelSent(
    long orderId,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const IBAuthoritativeOrderProjectionResult projection =
        m_projector.ProjectCancelSent(orderId, observedAtMs);
    ApplyProjectionLocked(orderId, projection);
    return projection;
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOpenOrderConsumer::ProjectOrderStatus(
    long orderId,
    const std::string& status,
    double filledQuantity,
    double remainingQuantity,
    double averageFillPrice,
    bool executionEvidence,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const IBAuthoritativeOrderProjectionResult projection = m_projector.ProjectOrderStatus(
        orderId, status, filledQuantity, remainingQuantity,
        averageFillPrice, executionEvidence, observedAtMs);
    ApplyProjectionLocked(orderId, projection);
    return projection;
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOpenOrderConsumer::ConsumeOpenOrder(
    const IBEvent& event,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    IBAuthoritativeOrderProjectionResult projection;
    if (!event.account.empty() && !m_configuredAccount.empty() &&
        event.account != m_configuredAccount)
        return projection;
    projection = m_projector.ProjectOpenOrder(event, m_configuredAccount, observedAtMs);
    if (m_generation != 0 && projection.status == IBAuthoritativeOrderProjectionStatus::Rejected)
    {
        m_rejected = true;
        m_rejectReason = projection.reasonCode;
    }
    ApplyProjectionLocked(static_cast<long>(event.id), projection);
    return projection;
}

IBAuthoritativeOpenOrderCompletion IBAuthoritativeOpenOrderConsumer::CompleteRefresh(
    std::uint64_t generation,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    IBAuthoritativeOpenOrderCompletion result;
    if (generation == 0 || generation != m_generation)
    {
        result.reasonCode = "STALE_OPEN_ORDER_GENERATION";
        return result;
    }
    for (std::map<long, AuthoritativeActiveOrder>::const_iterator it = m_orders.begin();
         it != m_orders.end(); ++it)
        result.orders.push_back(it->second);
    if (m_rejected)
        result.reasonCode = m_rejectReason.empty() ? "OPEN_ORDER_PROJECTION_INCOMPLETE" : m_rejectReason;
    else
    {
        const AuthoritativeSnapshotWriteResult write = m_store.ReplaceActiveOrders(
            result.orders, observedAtMs, "ib.open_orders");
        result.accepted = write.accepted;
        result.reasonCode = write.reasonCode;
    }
    m_generation = 0;
    m_rejected = false;
    m_rejectReason.clear();
    m_orders.clear();
    return result;
}
