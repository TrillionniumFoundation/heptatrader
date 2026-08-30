#pragma once

// Force-included only for hepta_unix_tool_server_tests. Include every header
// used by that translation unit before defining the narrow test-call macros,
// so system and project declarations remain untouched.
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tool_host/unix_tool_client.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace hepta_unix_tool_server_test_detail
{
inline std::mutex pressureMutex;
inline std::condition_variable pressureCondition;
inline std::set<int> connectedClientSockets;
inline std::atomic<bool> pressureBarrierUsed(false);

inline int Connect(int socket,
                   const struct sockaddr* address,
                   socklen_t addressLength)
{
    const int result = ::connect(socket, address, addressLength);
    if (result == 0)
    {
        std::lock_guard<std::mutex> lock(pressureMutex);
        connectedClientSockets.insert(socket);
        pressureCondition.notify_all();
    }
    return result;
}

inline int Close(int descriptor)
{
    {
        std::lock_guard<std::mutex> lock(pressureMutex);
        connectedClientSockets.erase(descriptor);
    }
    return ::close(descriptor);
}

inline int Usleep(useconds_t microseconds)
{
    if (microseconds != 150000 || pressureBarrierUsed.load())
        return ::usleep(microseconds);

    std::unique_lock<std::mutex> lock(pressureMutex);
    const bool fourClientsConnected = pressureCondition.wait_for(
        lock,
        std::chrono::seconds(2),
        []() { return connectedClientSockets.size() >= 4; });
    if (!fourClientsConnected || pressureBarrierUsed.exchange(true))
    {
        lock.unlock();
        return ::usleep(microseconds);
    }
    lock.unlock();

    // The pressure clients have all completed connect() and cannot close until
    // a response arrives. Keep the first same-owner request active while the
    // two ingress workers fill the configured two-entry owner queue and reject
    // the fourth request. This is a source-level test barrier, not a runtime
    // scheduling or queue-limit change.
    return ::usleep(1000000);
}
}

#define connect(...) \
    ::hepta_unix_tool_server_test_detail::Connect(__VA_ARGS__)
#define close(...) \
    ::hepta_unix_tool_server_test_detail::Close(__VA_ARGS__)
#define usleep(...) \
    ::hepta_unix_tool_server_test_detail::Usleep(__VA_ARGS__)
