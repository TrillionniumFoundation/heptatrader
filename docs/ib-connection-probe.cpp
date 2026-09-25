// Read-only, process-bounded IB API connectivity diagnostic. This is not
// PAPER qualification: endpoint numbers and startup callbacks grant no authority.
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <mutex>
#include <string>
#include <thread>

#include "DefaultEWrapper.h"
#include "EClientSocket.h"
#include "EReaderOSSignal.h"
#include "EReader.h"

namespace {
struct Observation {
    std::atomic<bool> handshake{false}, nextId{false}, accounts{false};
    std::atomic<bool> timeRequested{false}, timeReceived{false};
    std::atomic<int> errorCode{0}, phase{0};
};

bool ParsePositive(const char* value, unsigned maximum, unsigned& result) {
    if (value == nullptr || *value == '\0') return false;
    unsigned parsed = 0;
    for (const char* p = value; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
        const unsigned digit = static_cast<unsigned>(*p - '0');
        if (parsed > (maximum - digit) / 10) return false;
        parsed = parsed * 10 + digit;
    }
    if (parsed == 0 || parsed > maximum) return false;
    result = parsed;
    return true;
}

void Emit(const char* status, const char* reason, const Observation& state) {
    // Never print Broker error text, account identifiers, next order IDs or
    // credentials. Only fixed vocabulary, booleans and the numeric SDK code.
    std::printf("{\"schema\":\"heptatrader.ib-connectivity.v1\","
        "\"status\":\"%s\",\"reason\":\"%s\",\"sdk_error_code\":%d,"
        "\"handshake_received\":%s,\"next_valid_id_received\":%s,"
        "\"managed_accounts_received\":%s,\"current_time_received\":%s,"
        "\"broker_mutations\":0,\"broker_mode_verified\":false,"
        "\"paper_authorized\":false,\"live_authorized\":false}\n",
        status, reason, state.errorCode.load(), state.handshake ? "true" : "false",
        state.nextId ? "true" : "false", state.accounts ? "true" : "false",
        state.timeReceived ? "true" : "false");
    std::fflush(stdout);
}

class Deadline {
public:
    Deadline(unsigned timeoutMs, Observation& state) : m_state(state) {
        m_deadline = std::chrono::steady_clock::now() +
            std::chrono::milliseconds(timeoutMs);
        m_thread = std::thread([this] {
            std::unique_lock<std::mutex> lock(m_mutex);
            if (m_changed.wait_until(lock, m_deadline, [this] { return m_finished; })) return;
            const int phase = m_state.phase.load();
            Emit("TIMEOUT", phase == 0 ? "CONNECT_HANDSHAKE_TIMEOUT" :
                (phase == 1 ? "STARTUP_CALLBACK_TIMEOUT" : "READ_ROUNDTRIP_TIMEOUT"), m_state);
            // This isolated diagnostic never creates an order or owns a durable
            // writer. Terminate the whole process, including a blocked eConnect
            // or SDK cleanup, rather than detach work past a reported timeout.
            std::_Exit(2);
        });
    }
    ~Deadline() {
        { std::lock_guard<std::mutex> lock(m_mutex); m_finished = true; }
        m_changed.notify_one();
        if (m_thread.joinable()) m_thread.join();
    }
    int Complete(int code, const char* reason) {
        std::lock_guard<std::mutex> lock(m_mutex);
        // A delayed watchdog thread must not let a late result become READY.
        if (std::chrono::steady_clock::now() >= m_deadline) {
            Emit("TIMEOUT", "TOTAL_DEADLINE_EXCEEDED", m_state);
            code = 2;
        } else Emit(code == 0 ? "READY" : "FAILED", reason, m_state);
        m_finished = true;
        m_changed.notify_one();
        return code;
    }
private:
    Observation& m_state;
    std::mutex m_mutex;
    std::condition_variable m_changed;
    bool m_finished = false;
    std::thread m_thread;
    std::chrono::steady_clock::time_point m_deadline;
};

class ProbeWrapper : public DefaultEWrapper {
public:
    explicit ProbeWrapper(Observation& state) : m_state(state) {}
    void error(int, int code, const std::string&, const std::string&) override {
        m_state.errorCode = code;
    }
    void nextValidId(OrderId id) override { m_state.nextId = id >= 0; }
    void managedAccounts(const std::string& accounts) override {
        m_state.accounts = accounts.find_first_not_of(" ,\t\r\n") != std::string::npos;
    }
    void currentTime(long value) override {
        if (m_state.timeRequested && value > 0) m_state.timeReceived = true;
    }
private:
    Observation& m_state;
};

int Probe(const char* host, unsigned port, unsigned clientId,
          Observation& state, const char*& reason) {
    ProbeWrapper wrapper(state);
    EReaderOSSignal signal(50);
    EClientSocket client(&wrapper, &signal);
    if (!client.eConnect(host, static_cast<int>(port), static_cast<int>(clientId), false)) {
        reason = "CONNECT_FAILED";
        return 1;
    }
    state.handshake = true;
    state.phase = 1;
    EReader reader(&client, &signal);
    reader.start();
    while (client.isConnected()) {
        signal.waitForSignal();
        reader.processMsgs();
        if (state.nextId && state.accounts && !state.timeRequested) {
            state.phase = 2;
            state.timeRequested = true;
            client.reqCurrentTime(); // The only explicit post-startup request.
        }
        if (state.nextId && state.accounts && state.timeReceived) {
            client.eDisconnect();
            reason = "READ_ONLY_ROUNDTRIP_COMPLETE";
            return 0;
        }
    }
    client.eDisconnect();
    reason = "DISCONNECTED_BEFORE_READINESS";
    return 3;
}
} // namespace

int main(int argc, char** argv) {
    Observation state;
    const char* host = argc > 1 ? argv[1] : "127.0.0.1";
    unsigned port = 7497, clientId = 101, timeoutMs = 8000;
    if (argc > 5 || std::string(host) != "127.0.0.1" ||
        (argc > 2 && !ParsePositive(argv[2], 65535, port)) ||
        port == 4001 || port == 7496 ||
        (argc > 3 && !ParsePositive(argv[3], std::numeric_limits<int>::max(), clientId)) ||
        (argc > 4 && !ParsePositive(argv[4], 30000, timeoutMs))) {
        Emit("INVALID_ARGUMENT", "EXPECTED_LOOPBACK_PORT_CLIENT_ID_TIMEOUT", state);
        return 64;
    }
    // Starts before DNS/socket negotiation, not after eConnect returns. The
    // budget includes callbacks, the read round trip and SDK destruction.
    Deadline deadline(timeoutMs, state);
    const char* reason = "SDK_EXCEPTION";
    int code = 4;
    try { code = Probe(host, port, clientId, state, reason); }
    catch (...) { reason = "SDK_EXCEPTION"; }
    return deadline.Complete(code, reason);
}
