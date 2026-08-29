#pragma once

#include "execution_event_hub.h"

#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

struct OwnerScopedHealthTarget
{
	std::string executionDomain;
	std::string agentId;
	std::string sessionId;
	std::string venue;
};

class OwnerScopedHealthPublisher
{
public:
	typedef std::function<std::vector<OwnerScopedHealthTarget>()> TargetProvider;

	OwnerScopedHealthPublisher(ExecutionEventHub& eventHub,
		const TargetProvider& targetProvider);

	std::size_t PublishIfChanged(const std::string& status,
		const std::string& healthSignature);
	std::uint64_t Publish(const OwnerScopedHealthTarget& target,
		const std::string& status, const std::string& reasonCode);
	std::uint64_t PublishAggregated(const OwnerScopedHealthTarget& target,
		const std::string& status, const std::string& reasonCode,
		std::uint64_t nowMs, std::uint64_t debounceMs);

private:
	ExecutionEventHub& m_eventHub;
	TargetProvider m_targetProvider;
	std::mutex m_mutex;
	std::string m_lastHealthSignature;
	struct AggregateState
	{
		std::uint64_t lastPublishedMs = 0;
		std::uint64_t suppressed = 0;
	};
	std::unordered_map<std::string, AggregateState> m_aggregates;
};
