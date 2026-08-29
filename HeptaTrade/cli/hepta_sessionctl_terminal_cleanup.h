#pragma once

#include <cstdint>
#include <string>

struct HeptaSessionCtlTerminalCleanupCommand
{
    std::string storePath;
    std::string keyPath;
    std::string backupPath;
    std::string cleanupLockPath;
    std::string expectedIssuer;
    std::string expectedAgentId;
    std::uint32_t expectedPeerUid = 0;
    std::uint32_t expectedSourceUid = 0;
    std::uint32_t expectedSourceGid = 0;
    std::uint32_t expectedSourceMode = 0;
    std::string expectedPreStoreSha256;
    std::uint32_t expectedKeyUid = 0;
    std::uint32_t expectedKeyGid = 0;
    std::uint32_t expectedKeyMode = 0;
    std::string expectedKeyFileSha256;
};

class HeptaSessionCtlTerminalCleanup
{
public:
    static bool IsCommand(int argc, char** argv);
    static bool Parse(int argc, char** argv,
                      HeptaSessionCtlTerminalCleanupCommand& command,
                      std::string& reason);
    static int Run(int argc, char** argv);
    static const char* Usage();
};
