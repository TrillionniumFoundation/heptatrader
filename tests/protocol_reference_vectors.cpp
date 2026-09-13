#include "../HeptaTrade/execution/execution_service_protocol.h"
#include "../HeptaTrade/execution/execution_event_feed_contract.h"
#include "../HeptaTrade/tool_host/session_supervisor_protocol.h"
#include <cstdlib>
#include <iostream>
#include <string>

static void Check(bool result) { if (!result) std::abort(); }
static std::string Hex(const char* input) {
    std::string out;
    while (*input) { std::string pair(input, 2); out += static_cast<char>(std::strtoul(pair.c_str(), nullptr, 16)); input += 2; }
    return out;
}
static std::string Tlv(unsigned tag, const std::string& value) {
    std::string out;
    out += static_cast<char>(tag >> 8); out += static_cast<char>(tag);
    for (int shift = 24; shift >= 0; shift -= 8) out += static_cast<char>(value.size() >> shift);
    return out + value;
}
int main() {
    std::string reason, encoded;
    ExecutionServiceRequest execution;
    const std::string identity = Hex("48455831000a0007");
    Check(ExecutionServiceProtocol::DecodeRequest(identity, execution, reason));
    Check(execution.operation == ExecutionServiceOperation::GetServiceIdentity);
    Check(ExecutionServiceProtocol::EncodeRequest(execution, encoded, reason));
    Check(encoded == identity);
    Check(!ExecutionServiceProtocol::DecodeRequest(Hex("4845583100090007"), execution, reason));
    Check(!ExecutionServiceProtocol::DecodeRequest(identity + Tlv(1, "unexpected"), execution, reason));
    ExecutionEventFeedRequest feed;
    const std::string eventIdentity = Hex("4845563200020001");
    Check(ExecutionEventFeedProtocol::DecodeRequest(eventIdentity, feed, reason));
    Check(ExecutionEventFeedProtocol::EncodeRequest(feed, encoded, reason));
    Check(encoded == eventIdentity);
    Check(!ExecutionEventFeedProtocol::DecodeRequest(Hex("4845563200010001"), feed, reason));
    SessionSupervisorRequest session;
    const std::string revoke = "HSS1" + Tlv(1, "revoke") + Tlv(3, "inert-token") + Tlv(10, "1");
    Check(SessionSupervisorProtocol::DecodeRequest(revoke, session, reason));
    Check(session.operation == SessionSupervisorOperation::Revoke);
    Check(session.expectedGeneration == 1);
    Check(SessionSupervisorProtocol::EncodeRequest(session, encoded, reason));
    Check(encoded == revoke);
    Check(!SessionSupervisorProtocol::DecodeRequest(revoke + Tlv(3, "duplicate"), session, reason));
    Check(!SessionSupervisorProtocol::DecodeRequest(revoke + Tlv(7, "100"), session, reason));
    Check(!SessionSupervisorProtocol::DecodeRequest("HSS1" + Tlv(1, "revoke") + Tlv(3, "inert-token"), session, reason));
    std::cout << "protocol golden vectors PASS: HSS1 / HEX1 v10 / HEV2 v2; no I/O\n";
}
