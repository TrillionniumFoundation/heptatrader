#include "owner_scoped_health_publisher.h"

#include <sstream>

OwnerScopedHealthPublisher::OwnerScopedHealthPublisher(
	ExecutionEventHub& eventHub, const TargetProvider& targetProvider)
	: m_eventHub(eventHub), m_targetProvider(targetProvider)
{
}

std::size_t OwnerScopedHealthPublisher::PublishIfChanged(
	const std::string& status, const std::string& healthSignature)
{
	{
		std::lock_guard<std::mutex> lock(m_mutex);
		if (healthSignature == m_lastHealthSignature) return 0;
		m_lastHealthSignature = healthSignature;
	}
	if (!m_targetProvider) return 0;
	const std::vector<OwnerScopedHealthTarget> targets = m_targetProvider();
	std::size_t published = 0;
	for (std::size_t i = 0; i < targets.size(); ++i)
		if (Publish(targets[i], status, healthSignature) != 0) ++published;
	return published;
}

std::uint64_t OwnerScopedHealthPublisher::Publish(
	const OwnerScopedHealthTarget& target, const std::string& status,
	const std::string& reasonCode)
{
	ExecutionEvent event;
	event.executionDomain = target.executionDomain;
	event.agentId = target.agentId;
	event.sessionId = target.sessionId;
	event.type = "system.health";
	event.venue = target.venue;
	event.status = status;
	event.reasonCode = reasonCode;
	return m_eventHub.Publish(event);
}

std::uint64_t OwnerScopedHealthPublisher::PublishAggregated(
	const OwnerScopedHealthTarget& target, const std::string& status,
	const std::string& reasonCode, std::uint64_t nowMs, std::uint64_t debounceMs)
{
	const std::string key = target.executionDomain + "\n" + target.agentId + "\n" +
		target.sessionId + "\n" + status + "\n" + reasonCode;
	std::uint64_t count = 1;
	{
		std::lock_guard<std::mutex> lock(m_mutex);
		AggregateState& state = m_aggregates[key];
		if (state.lastPublishedMs != 0 && nowMs >= state.lastPublishedMs &&
			nowMs - state.lastPublishedMs < debounceMs)
		{
			++state.suppressed;
			return 0;
		}
		count += state.suppressed;
		state.suppressed = 0;
		state.lastPublishedMs = nowMs;
	}
	std::ostringstream aggregated;
	aggregated << reasonCode << ":count=" << count;
	return Publish(target, status, aggregated.str());
}
