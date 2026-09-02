#include "heptactl_command.h"
#include "heptactl_exit_codes.h"
#include "../client/native_tool_client.h"
#include "heptatrader_version.h"

#include <iostream>
#include <chrono>
#include <thread>

namespace
{
int EmitFailure(const NativeToolClientResult& result, const std::string& reason)
{
    if (!reason.empty())
    {
        std::cerr << reason << std::endl;
        return HeptaCtlExitCodes::FromClientFailure(reason);
    }
    std::cout << result.responseJson << std::endl;
    return HeptaCtlExitCodes::FromResult(result.envelope);
}

bool WatchCall(NativeToolClient& client,
               const std::string& callId,
               const std::string& tool,
               const std::string& target,
               const std::string& instrument,
               NativeToolClientResult& result,
               std::string& reason)
{
    for (unsigned int attempt = 0; attempt < 2; ++attempt)
    {
        TradingToolHostRequest request;
        request.toolCallId = attempt == 0 ? callId : callId + "-retry1";
        request.call.name = tool;
        request.call.targetToolName = target;
        request.call.instrument = instrument;
        if (client.Call(request, result, reason)) return true;
        const bool retryable =
            reason == "SOCKET_CONNECT_FAILED" ||
            reason == "FRAME_WRITE_TIMEOUT" ||
            reason == "FRAME_HEADER_TIMEOUT" ||
            reason == "FRAME_BODY_TIMEOUT";
        if (!retryable || attempt != 0) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    return false;
}

int RunWatchSnapshot(const HeptaCtlCommand& command, NativeToolClient& client)
{
    NativeToolClientResult catalog;
    NativeToolClientResult snapshot;
    std::string reason;
    const std::string prefix = command.request.toolCallId + "-watch";
    if (!WatchCall(client, prefix + "-catalog", "system.tools.list", "", "",
                   catalog, reason) || catalog.envelope.status != "ok")
        return EmitFailure(catalog, reason);
    if (!WatchCall(client, prefix + "-snapshot", "watch.get_snapshot", "",
                   command.watchInstrument, snapshot, reason) ||
        snapshot.envelope.status != "ok")
        return EmitFailure(snapshot, reason);
    std::cout << snapshot.envelope.payloadJson << std::endl;
    return HeptaCtlSuccess;
}
}

int main(int argc, char** argv)
{
    if (argc == 2 && std::string(argv[1]) == "--version")
    {
        std::cout << HEPTA_VERSION_FULL << std::endl;
        return HeptaCtlSuccess;
    }

    HeptaCtlCommand command;
    std::string reason;
    if (!HeptaCtlCommandParser::Parse(argc, argv, command, reason))
    {
        std::cerr << reason << '\n' << HeptaCtlCommandParser::Usage() << std::endl;
        return HeptaCtlUsageOrCredential;
    }
    NativeToolClientConfig config;
    config.socketPath = command.socketPath;
    config.tokenFile = command.tokenFile;
    config.sessionToken = command.sessionToken;
    config.timeoutMs = command.ioTimeoutMs;
    NativeToolClient client(config);
    NativeToolClientResult result;
    if (command.watchSnapshot)
        return RunWatchSnapshot(command, client);
    if (command.request.call.name == "system.tools.describe")
    {
        TradingToolHostRequest discovery;
        discovery.toolCallId = command.request.toolCallId + "-catalog";
        discovery.call.name = "system.tools.list";
        NativeToolClientResult discoveryResult;
        if (!client.Call(discovery, discoveryResult, reason))
        {
            std::cerr << reason << std::endl;
            return HeptaCtlExitCodes::FromClientFailure(reason);
        }
        if (discoveryResult.envelope.status != "ok")
        {
            std::cout << discoveryResult.responseJson << std::endl;
            return HeptaCtlExitCodes::FromResult(discoveryResult.envelope);
        }
    }
    if (!client.Call(command.request, result, reason))
    {
        std::cerr << reason << std::endl;
        return HeptaCtlExitCodes::FromClientFailure(reason);
    }
    std::cout << result.responseJson << std::endl;
    return HeptaCtlExitCodes::FromResult(result.envelope);
}
