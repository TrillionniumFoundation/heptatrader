#pragma once

#include "../tool_host/session_supervisor_protocol.h"

#include <cstdint>
#include <string>

struct HeptaSessionCtlCommand
{
    std::string socketPath;
    std::string tokenFile;
    std::string replacementTokenFile;
    std::string terminalEvidenceFile;
    bool hasTokenOwnerUid = false;
    std::uint32_t tokenOwnerUid = 0;
    int ioTimeoutMs = 5000;
    SessionSupervisorRequest request;
};

class HeptaSessionCtlCommandParser
{
public:
    static bool Parse(int argc, char** argv,
                      HeptaSessionCtlCommand& command,
                      std::string& reason);
    static bool ReadTokenFile(const std::string& path,
                              bool hasExpectedOwnerUid,
                              std::uint32_t expectedOwnerUid,
                              std::string& token,
                              std::string& reason);
    static bool ReadTerminalEvidenceFile(
                              const std::string& path,
                              const std::string& expectedSha256,
                              std::string& evidence,
                              std::string& reason);
    static const char* Usage();
};
