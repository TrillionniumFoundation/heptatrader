#pragma once

#include "execution_event_feed_contract.h"

#include <chrono>
#include <cstddef>
#include <string>

namespace HeptaExecutionEventFeedTransport
{
typedef std::chrono::steady_clock::time_point Deadline;

bool ValidIdentity(const ExecutionServiceIdentity& identity);
bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right);
int RemainingMs(const Deadline& deadline);
bool WaitFd(int fd, short events, const Deadline& deadline);
bool ReadFrame(int fd,
               std::size_t maxBytes,
               const Deadline& deadline,
               std::string& body);
bool WriteFrame(int fd,
                const std::string& body,
                const Deadline& deadline);
} // namespace HeptaExecutionEventFeedTransport
