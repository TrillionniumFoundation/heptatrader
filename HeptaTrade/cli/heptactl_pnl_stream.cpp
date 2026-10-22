#include "heptactl_pnl_stream.h"
#include "heptactl_exit_codes.h"

#include <cstdint>
#include <istream>
#include <limits>
#include <ostream>

int RunPnlReadStream(const HeptaCtlCommand& command, NativeToolClient& client,
                     std::istream& input, std::ostream& output,
                     std::ostream& errors)
{
    std::uint64_t sequence = 0;
    for (;;)
    {
        // Do not let an unbounded stdin line allocate an unbounded buffer.
        std::string line;
        char byte = 0;
        bool complete = false;
        while (input.get(byte))
        {
            if (byte == '\n') { complete = true; break; }
            if (line.size() == 256)
            { errors << "PNL_READ_STREAM_LINE_TOO_LARGE\n"; return HeptaCtlUsageOrCredential; }
            line += byte;
        }
        if (!complete)
        {
            if (input.eof() && line.empty()) return HeptaCtlSuccess;
            errors << "PNL_READ_STREAM_INCOMPLETE_LINE\n";
            return HeptaCtlUsageOrCredential;
        }

        TradingToolHostRequest request = command.request;
        request.call = TradingToolCall();
        if (line == "system.get_health" || line == "orders.list" ||
            line == "portfolio.list_positions")
            request.call.name = line;
        else if (line == "market.get_quote EUR.USD" ||
                 line == "market.get_quote BTC.USD")
        {
            request.call.name = "market.get_quote";
            request.call.instrument = line.substr(17);
        }
        else
        { errors << "PNL_READ_STREAM_TOOL_FORBIDDEN\n"; return HeptaCtlUsageOrCredential; }

        if (sequence == std::numeric_limits<std::uint64_t>::max())
        { errors << "PNL_READ_STREAM_SEQUENCE_EXHAUSTED\n"; return HeptaCtlUsageOrCredential; }
        request.toolCallId = command.request.toolCallId.substr(0, 90) +
            "-pnl-" + std::to_string(++sequence);
        NativeToolClientResult result;
        std::string reason;
        // No retry. The same original client validates each read's schema and
        // credential binding; only its verified discovery data is reused.
        if (!client.Call(request, result, reason))
        {
            errors << reason << '\n';
            return HeptaCtlExitCodes::FromClientFailure(reason);
        }
        output << result.responseJson << '\n';
        output.flush();
        if (!output) return HeptaCtlTransportOrProtocol;
        if (result.envelope.status != "ok")
            return HeptaCtlExitCodes::FromResult(result.envelope);
    }
}
