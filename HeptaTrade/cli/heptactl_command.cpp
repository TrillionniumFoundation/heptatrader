#include "heptactl_command.h"

#include <cerrno>
#include <cstdlib>
#include <ctime>
#include <cmath>
#include <locale>
#include <sstream>
#include <unistd.h>

namespace {

bool CanonicalSignedInteger(const std::string& value)
{
    if (value.empty()) return false;
    const std::size_t offset = value[0] == '-' ? 1u : 0u;
    if (offset == value.size() ||
        (value[offset] == '0' &&
         (offset != 0 || offset + 1u < value.size()))) return false;
    for (std::size_t i = offset; i < value.size(); ++i)
        if (value[i] < '0' || value[i] > '9') return false;
    return true;
}

bool CanonicalFloating(const std::string& value)
{
    if (value.empty()) return false;
    std::size_t offset = value[0] == '-' ? 1u : 0u;
    if (offset == value.size()) return false;
    if (value[offset] == '0')
    {
        ++offset;
        if (offset < value.size() && value[offset] >= '0' &&
            value[offset] <= '9') return false;
    }
    else
    {
        if (value[offset] < '1' || value[offset] > '9') return false;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
    }
    if (offset < value.size() && value[offset] == '.')
    {
        ++offset;
        const std::size_t fractionStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == fractionStart) return false;
    }
    if (offset < value.size() &&
        (value[offset] == 'e' || value[offset] == 'E'))
    {
        ++offset;
        if (offset < value.size() &&
            (value[offset] == '+' || value[offset] == '-')) ++offset;
        const std::size_t exponentStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == exponentStart) return false;
    }
    return offset == value.size();
}

bool ParseLongLong(const std::string& value, long long& out)
{
    if (!CanonicalSignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = parsed;
    return true;
}

bool ParseDouble(const std::string& value, double& out)
{
    if (!CanonicalFloating(value)) return false;
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    input >> std::noskipws;
    double parsed = 0.0;
    input >> parsed;
    if (!input || !input.eof() || !std::isfinite(parsed)) return false;
    out = parsed;
    return true;
}

bool ApplyField(const std::string& argument, HeptaCtlCommand& command, std::string& reason)
{
    const std::string::size_type separator = argument.find('=');
    if (separator == std::string::npos || separator == 0 || separator + 1 == argument.size())
    {
        reason = "EXPECTED_KEY_VALUE:" + argument;
        return false;
    }
    const std::string key = argument.substr(0, separator);
    const std::string value = argument.substr(separator + 1);
    TradingToolCall& call = command.request.call;
    long long integer = 0;
    double decimal = 0.0;

    if (key == "instrument") call.instrument = value;
    else if (key == "tool_name") call.targetToolName = value;
    else if (key == "command_id") call.targetCommandId = value;
    else if (key == "symbol") call.ibContract.symbol = value;
    else if (key == "currency") call.ibContract.currency = value;
    else if (key == "sec_type") call.ibContract.secType = value;
    else if (key == "exchange") call.ibContract.exchange = value;
    else if (key == "side") call.ibOrder.action = value;
    else if (key == "order_type") call.ibOrder.orderType = value;
    else if (key == "tif") call.timeInForce = value;
    else if (key == "preview_permit") call.previewPermit = value;
    else if (key == "order_id" && ParseLongLong(value, integer)) call.orderId = static_cast<long>(integer);
    else if (key == "quantity" && ParseDouble(value, decimal)) call.ibOrder.totalQuantity = decimal;
    else if (key == "limit_price" && ParseDouble(value, decimal)) call.ibOrder.lmtPrice = decimal;
    else if (key == "reference_price" && ParseDouble(value, decimal)) call.referencePrice = decimal;
    else if (key == "expires_at_ms" && ParseLongLong(value, integer)) call.expiresAtMs = integer;
    else if (key == "timeout_ms" && ParseLongLong(value, integer)) call.waitTimeoutMs = static_cast<int>(integer);
    else if (key == "after_sequence" && ParseLongLong(value, integer) && integer >= 0)
        call.afterEventSequence = static_cast<std::uint64_t>(integer);
    else if (key == "queue_deadline_at_ms" && ParseLongLong(value, integer) && integer > 0)
        command.request.queueDeadlineAtMs = static_cast<std::uint64_t>(integer);
    else
    {
        reason = "INVALID_OR_UNKNOWN_FIELD:" + key;
        return false;
    }
    return true;
}

}

const char* HeptaCtlCommandParser::Usage()
{
    return "usage: heptactl [--socket PATH] [--token-file PATH] [--call-id ID] "
           "[--protocol-min N] [--protocol-max N] [--schema-hash SHA256] [--io-timeout-ms N] "
           "tools list|tools describe TOOL|call TOOL [key=value ...]|"
           "wait [after_sequence=N] [timeout_ms=N]|cancel TOOL_CALL_ID|"
           "watch snapshot EUR.USD";
}

bool HeptaCtlCommandParser::Parse(int argc, char** argv, HeptaCtlCommand& command, std::string& reason)
{
    command = HeptaCtlCommand();
    const char* socketEnv = std::getenv("HEPTA_TOOL_SOCKET");
    const char* tokenEnv = std::getenv("HEPTA_TOOL_SESSION_TOKEN");
    if (socketEnv != nullptr) command.socketPath = socketEnv;
    if (tokenEnv != nullptr) command.sessionToken = tokenEnv;

    int index = 1;
    while (index < argc && std::string(argv[index]).find("--") == 0)
    {
        const std::string option = argv[index++];
        if (index >= argc) { reason = "MISSING_OPTION_VALUE:" + option; return false; }
        const std::string value = argv[index++];
        if (option == "--socket") command.socketPath = value;
        else if (option == "--token-file") command.tokenFile = value;
        else if (option == "--call-id") command.request.toolCallId = value;
        else if (option == "--schema-hash") command.request.expectedSchemaHash = value;
        else if (option == "--protocol-min" || option == "--protocol-max")
        {
            long long parsed = 0;
            if (!ParseLongLong(value, parsed) || parsed < 1 || parsed > 65535)
            { reason = "INVALID_PROTOCOL_VERSION"; return false; }
            if (option == "--protocol-min") command.request.protocolMinVersion = static_cast<unsigned int>(parsed);
            else command.request.protocolMaxVersion = static_cast<unsigned int>(parsed);
        }
        else if (option == "--io-timeout-ms")
        {
            long long parsed = 0;
            if (!ParseLongLong(value, parsed) || parsed < 1 || parsed > 120000)
            { reason = "INVALID_IO_TIMEOUT"; return false; }
            command.ioTimeoutMs = static_cast<int>(parsed);
        }
        else { reason = "UNKNOWN_OPTION:" + option; return false; }
    }

    if (index >= argc) { reason = "MISSING_COMMAND"; return false; }
    const std::string verb = argv[index++];
    if (verb == "tools")
    {
        if (index >= argc) { reason = "MISSING_TOOLS_SUBCOMMAND"; return false; }
        const std::string subcommand = argv[index++];
        if (subcommand == "list") command.request.call.name = "system.tools.list";
        else if (subcommand == "describe" && index < argc)
        {
            command.request.call.name = "system.tools.describe";
            command.request.call.targetToolName = argv[index++];
        }
        else { reason = "INVALID_TOOLS_SUBCOMMAND"; return false; }
    }
    else if (verb == "call" && index < argc) command.request.call.name = argv[index++];
    else if (verb == "wait") command.request.call.name = "events.wait";
    else if (verb == "cancel" && index < argc)
    {
        command.request.call.name = "system.cancel_request";
        command.request.cancelToolCallId = argv[index++];
    }
    else if (verb == "watch" && index + 1 < argc &&
             std::string(argv[index]) == "snapshot")
    {
        ++index;
        command.watchSnapshot = true;
        command.watchInstrument = argv[index++];
        if (command.watchInstrument != "EUR.USD")
        {
            reason = "WATCH_INSTRUMENT_FORBIDDEN";
            return false;
        }
    }
    else { reason = "INVALID_COMMAND"; return false; }

    while (!command.watchSnapshot && index < argc)
        if (!ApplyField(argv[index++], command, reason)) return false;
    if (index != argc) { reason = "UNEXPECTED_ARGUMENT"; return false; }

    if (command.socketPath.empty()) { reason = "MISSING_SOCKET"; return false; }
    if (command.tokenFile.empty() && command.sessionToken.empty()) { reason = "MISSING_SESSION_TOKEN"; return false; }
    if (command.request.toolCallId.empty())
    {
        std::ostringstream id;
        id.imbue(std::locale::classic());
        id << "heptactl-" << static_cast<unsigned long long>(std::time(nullptr)) << '-' << getpid();
        command.request.toolCallId = id.str();
    }
    reason.clear();
    return true;
}
