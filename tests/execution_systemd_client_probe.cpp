#include "../HeptaTrade/execution/execution_event_feed.h"
#include "../HeptaTrade/execution/unix_execution_service.h"

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <string>

namespace
{
const char* const kExecutionSocket = "/run/hepta-execution/execution.sock";
const char* const kEventSocket = "/run/hepta-execution/events.sock";

struct Options
{
    std::uint32_t serverUid = 0;
    std::string executionDomain;
    std::string agentId;
    std::string sessionId;
    std::string plane = "both";
    int timeoutMs = 100;
};

void Usage(const char* program)
{
    std::cerr << "usage: " << program
              << " --server-uid UID --execution-domain DOMAIN"
              << " --agent-id AGENT --session-id SESSION"
              << " [--plane both|execution|event]"
              << " [--timeout-ms 1..1000]\n";
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum,
                   std::uint64_t& parsed)
{
    if (value.empty()) return false;
    std::uint64_t result = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        if (character < static_cast<unsigned char>('0') ||
            character > static_cast<unsigned char>('9'))
            return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (result > (maximum - digit) / 10) return false;
        result = result * 10 + digit;
    }
    parsed = result;
    return true;
}

bool ValidOwnerComponent(const std::string& value)
{
    if (value.empty() || value.size() > 256) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        if (character < 0x20 || character == 0x7f) return false;
    }
    return true;
}

bool ParseOptions(int argc, char** argv, Options& options, std::string& reason)
{
    std::map<std::string, std::string> values;
    for (int i = 1; i < argc; i += 2)
    {
        if (i + 1 >= argc)
        {
            reason = "every option requires one value";
            return false;
        }
        const std::string key(argv[i]);
        if (key != "--server-uid" && key != "--execution-domain" &&
            key != "--agent-id" && key != "--session-id" &&
            key != "--plane" && key != "--timeout-ms")
        {
            reason = "unknown option: " + key;
            return false;
        }
        if (!values.insert(std::make_pair(key, std::string(argv[i + 1]))).second)
        {
            reason = "duplicate option: " + key;
            return false;
        }
    }

    static const char* required[] = {
        "--server-uid", "--execution-domain", "--agent-id", "--session-id"
    };
    for (std::size_t i = 0; i < sizeof(required) / sizeof(required[0]); ++i)
    {
        if (values.find(required[i]) == values.end())
        {
            reason = std::string("missing option: ") + required[i];
            return false;
        }
    }

    std::uint64_t parsed = 0;
    if (!ParseUnsigned(values["--server-uid"],
            std::numeric_limits<std::uint32_t>::max(), parsed) || parsed == 0)
    {
        reason = "server UID must be a nonzero uint32";
        return false;
    }
    options.serverUid = static_cast<std::uint32_t>(parsed);
    options.executionDomain = values["--execution-domain"];
    options.agentId = values["--agent-id"];
    options.sessionId = values["--session-id"];
    if (!ValidOwnerComponent(options.executionDomain) ||
        !ValidOwnerComponent(options.agentId) ||
        !ValidOwnerComponent(options.sessionId))
    {
        reason = "owner components must be bounded printable strings";
        return false;
    }
    const std::map<std::string, std::string>::const_iterator plane =
        values.find("--plane");
    if (plane != values.end()) options.plane = plane->second;
    if (options.plane != "both" && options.plane != "execution" &&
        options.plane != "event")
    {
        reason = "plane must be one of both, execution, or event";
        return false;
    }
    const std::map<std::string, std::string>::const_iterator timeout =
        values.find("--timeout-ms");
    if (timeout != values.end())
    {
        if (!ParseUnsigned(timeout->second, 1000, parsed) || parsed == 0)
        {
            reason = "timeout must be in [1, 1000] milliseconds";
            return false;
        }
        options.timeoutMs = static_cast<int>(parsed);
    }
    reason.clear();
    return true;
}

bool ValidIdentity(const ExecutionServiceIdentity& identity)
{
    return !identity.serviceEpoch.empty() && identity.serviceEpoch.size() <= 128 &&
        identity.serviceFencingGeneration != 0;
}

bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right)
{
    return left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration;
}

std::string JsonEscape(const std::string& value)
{
    std::string escaped;
    escaped.reserve(value.size());
    static const char hex[] = "0123456789abcdef";
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        switch (character)
        {
        case '\"': escaped.append("\\\""); break;
        case '\\': escaped.append("\\\\"); break;
        case '\b': escaped.append("\\b"); break;
        case '\f': escaped.append("\\f"); break;
        case '\n': escaped.append("\\n"); break;
        case '\r': escaped.append("\\r"); break;
        case '\t': escaped.append("\\t"); break;
        default:
            if (character < 0x20)
            {
                escaped.append("\\u00");
                escaped.push_back(hex[(character >> 4) & 0x0f]);
                escaped.push_back(hex[character & 0x0f]);
            }
            else
                escaped.push_back(static_cast<char>(character));
            break;
        }
    }
    return escaped;
}
}

int main(int argc, char** argv)
{
    Options options;
    std::string reason;
    if (!ParseOptions(argc, argv, options, reason))
    {
        Usage(argv[0]);
        std::cerr << "execution_systemd_client_probe: FAIL: " << reason << '\n';
        return 2;
    }

    const std::set<std::uint32_t> allowedServerUids{options.serverUid};
    // Keep transport admission/write time outside the bounded server-side wait.
    // Reusing the same absolute budget would make a legitimate Timeout response
    // race the client deadline and turn the read-only probe into a false red.
    const int ioTimeoutMs = options.timeoutMs + 1000;
    ExecutionServiceIdentity mutationIdentity;
    ExecutionServiceIdentity eventServiceIdentity;
    const bool queryMutation = options.plane != "event";
    const bool queryEvent = options.plane != "execution";
    if (queryMutation)
    {
        UnixExecutionServiceClient mutationClient(
            kExecutionSocket, ioTimeoutMs, 32768, allowedServerUids);
        if (!mutationClient.GetServiceIdentity(mutationIdentity, reason) ||
            !ValidIdentity(mutationIdentity))
        {
            std::cerr << "execution_systemd_client_probe: FAIL: mutation identity: "
                      << (reason.empty() ? "invalid identity" : reason) << '\n';
            return 3;
        }
    }

    std::string waitStatus = "not_run";
    if (queryEvent)
    {
        UnixExecutionEventFeedClient eventClient(
            kEventSocket, ioTimeoutMs, 32768, allowedServerUids);
        const ExecutionEventReadResult eventIdentity = eventClient.GetServiceIdentity();
        if (eventIdentity.status != ExecutionEventReadStatus::ServiceIdentity ||
            !ValidIdentity(eventIdentity.serviceIdentity))
        {
            std::cerr << "execution_systemd_client_probe: FAIL: event identity"
                      << " status=" << static_cast<int>(eventIdentity.status)
                      << " reason=" << eventIdentity.reasonCode << '\n';
            return 4;
        }
        eventServiceIdentity = eventIdentity.serviceIdentity;
        if (queryMutation && !SameIdentity(mutationIdentity, eventServiceIdentity))
        {
            std::cerr << "execution_systemd_client_probe: FAIL: shared identity mismatch\n";
            return 4;
        }

        ExecutionEventFeedRequest wait;
        wait.operation = ExecutionEventFeedOperation::Wait;
        wait.executionDomain = options.executionDomain;
        wait.agentId = options.agentId;
        wait.sessionId = options.sessionId;
        wait.expectedServiceIdentity = eventServiceIdentity;
        wait.afterSequence = 0;
        wait.timeoutMs = options.timeoutMs;
        const ExecutionEventReadResult waited = eventClient.Wait(wait);
        if (waited.status != ExecutionEventReadStatus::Timeout ||
            waited.reasonCode != "EXECUTION_EVENT_TIMEOUT" ||
            !SameIdentity(waited.serviceIdentity, eventServiceIdentity) ||
            waited.streamEpoch != eventServiceIdentity.serviceEpoch)
        {
            std::cerr << "execution_systemd_client_probe: FAIL: read-only wait"
                      << " status=" << static_cast<int>(waited.status)
                      << " reason=" << waited.reasonCode << '\n';
            return 5;
        }
        waitStatus = "timeout";
    }

    const ExecutionServiceIdentity& reportedIdentity = queryMutation ?
        mutationIdentity : eventServiceIdentity;
    std::cout << "execution_systemd_client_probe_evidence: {"
              << "\"schema\":\"hepta.execution-systemd-client-probe.v1\","
              << "\"plane\":\"" << options.plane << "\","
              << "\"service_epoch\":\""
              << JsonEscape(reportedIdentity.serviceEpoch) << "\","
              << "\"service_fencing_generation\":"
              << reportedIdentity.serviceFencingGeneration << ','
              << "\"mutation_identity_ok\":"
              << (queryMutation ? "true" : "false") << ','
              << "\"event_identity_ok\":"
              << (queryEvent ? "true" : "false") << ','
              << "\"shared_identity_ok\":"
              << (queryMutation && queryEvent ? "true" : "false") << ','
              << "\"wait_status\":\"" << waitStatus << "\","
              << "\"mutation_requests\":0}"
              << '\n';
    std::cout << "execution_systemd_client_probe: PASS\n";
    return 0;
}
