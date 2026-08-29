#pragma once

#include "session_supervisor_protocol.h"

#include <cstddef>
#include <string>

class UnixSessionSupervisorClient
{
public:
    static bool Call(const std::string& socketPath,
                     const SessionSupervisorRequest& request,
                     SessionSupervisorResult& result,
                     std::string& reason,
                     int timeoutMs = 5000,
                     std::size_t maxResponseBytes = 32768);
};
