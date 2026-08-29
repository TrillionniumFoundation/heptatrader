#pragma once

#include "execution_authority.h"

#include <string>

// Execution-side authorization bound to one OS-authenticated Gateway UID.
// The peer UID is checked by the Unix server; this record then prevents that
// Gateway from supplying another trust-domain's Agent/account/domain context.
// A runtime authority intentionally accepts exactly one such binding.
struct ExecutionGatewayContextBinding
{
    std::string agentId;
    std::string account;
    std::string venue;
    std::string executionDomain;

    bool Complete() const
    {
        return !agentId.empty() && !account.empty() && !venue.empty() &&
            !executionDomain.empty();
    }

    bool Matches(const AgentExecutionContext& context) const
    {
        return Complete() && context.agentId == agentId &&
            context.account == account && context.venue == venue &&
            context.executionDomain == executionDomain;
    }

    bool MatchesEventOwner(const std::string& candidateExecutionDomain,
                           const std::string& candidateAgentId) const
    {
        return Complete() && candidateAgentId == agentId &&
            candidateExecutionDomain == executionDomain;
    }
};
