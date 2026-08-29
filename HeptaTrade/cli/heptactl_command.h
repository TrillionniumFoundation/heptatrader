#pragma once

#include "../tool_host/trading_tool_host.h"

#include <string>

struct HeptaCtlCommand
{
    std::string socketPath;
    std::string tokenFile;
    std::string sessionToken;
    int ioTimeoutMs = 5000;
    bool watchSnapshot = false;
    std::string watchInstrument;
    TradingToolHostRequest request;
};

class HeptaCtlCommandParser
{
public:
    static bool Parse(int argc, char** argv, HeptaCtlCommand& command, std::string& reason);
    static const char* Usage();
};
