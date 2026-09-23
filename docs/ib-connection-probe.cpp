// ib-connection-probe.cpp
// Minimal IB API connectivity probe for TWS / IB Gateway.
// Build notes:
//   1) Supply a separately controlled IB C++ API source tree and point the
//      docs/ib_probe CMake configure step at it with -DIBAPI_ROOT=<path>.
//      The retired root Interface/ tree is not an SDK location.
//   2) Link the IB API client library as required by that pinned API package.

#include <iostream>
#include <thread>
#include <chrono>
#include <atomic>

#include "DefaultEWrapper.h"
#include "EClientSocket.h"
#include "EReaderOSSignal.h"
#include "EReader.h"

class ProbeWrapper : public DefaultEWrapper {
public:
    std::atomic<bool> connected{false};
    std::atomic<bool> gotNextValidId{false};

    void error(int id, int errorCode, const std::string& errorString, const std::string& advancedOrderRejectJson) override {
        std::cerr << "[IB][error] id=" << id << " code=" << errorCode << " msg=" << errorString << std::endl;
    }

    void nextValidId(OrderId orderId) override {
        std::cout << "[IB] nextValidId=" << orderId << std::endl;
        gotNextValidId = true;
    }

    void connectAck() override {
        std::cout << "[IB] connectAck" << std::endl;
        connected = true;
    }

};

int main(int argc, char** argv) {
    const char* host = "127.0.0.1";
    int port = 7497;      // TWS paper default
    int clientId = 101;

    if (argc >= 2) host = argv[1];
    if (argc >= 3) port = std::atoi(argv[2]);
    if (argc >= 4) clientId = std::atoi(argv[3]);

    std::cout << "[Probe] host=" << host << " port=" << port << " clientId=" << clientId << std::endl;

    ProbeWrapper wrapper;
    EReaderOSSignal signal(2000);
    EClientSocket client(&wrapper, &signal);

    if (!client.eConnect(host, port, clientId, false)) {
        std::cerr << "[Probe] eConnect failed" << std::endl;
        return 1;
    }

    EReader reader(&client, &signal);
    reader.start();

    auto t0 = std::chrono::steady_clock::now();
    while (std::chrono::steady_clock::now() - t0 < std::chrono::seconds(8)) {
        signal.waitForSignal();
        reader.processMsgs();
        if (wrapper.gotNextValidId.load()) break;
    }

    if (!wrapper.gotNextValidId.load()) {
        std::cerr << "[Probe] connected but no nextValidId within timeout" << std::endl;
        client.eDisconnect();
        return 2;
    }

    std::cout << "[Probe] IB connectivity OK" << std::endl;
    client.eDisconnect();
    return 0;
}
