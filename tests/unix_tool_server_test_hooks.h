#pragma once

// This header is preincluded only for hepta_unix_tool_server_tests.  It wraps
// the public test instance without changing UnixToolServer production code or
// ABI, allowing the interposer to observe the real queue-health counters.
#include "../HeptaTrade/tool_host/unix_tool_server.h"

extern "C" void hepta_test_set_unix_tool_server(UnixToolServer* server);

class HeptaTestUnixToolServer final : public UnixToolServer
{
public:
    explicit HeptaTestUnixToolServer(TradingToolHost& host)
        : UnixToolServer(host)
    {
        hepta_test_set_unix_tool_server(this);
    }

    ~HeptaTestUnixToolServer()
    {
        hepta_test_set_unix_tool_server(nullptr);
    }
};

// The real header has already been parsed.  Only subsequent source-level test
// declarations use the wrapper type.
#define UnixToolServer HeptaTestUnixToolServer
