// Developer-only process adapter. All credentials, HSR1 bytes, typed previews
// and socket calls belong to the existing SDK. This is not an execution service.
#include <hepta/research/native_strategy_client.h>
#include <tools/trading_tool_wire_contract.h>
#include <cmath>
#include <iostream>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {
using namespace hepta::research;
using Arguments = std::map<std::string, std::string>;
std::string Quoted(const std::string& text) {
    const char* digits = "0123456789abcdef";
    std::string out = "\"";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32 || c >= 127) { out += "\\u00"; out += digits[c >> 4]; out += digits[c & 15]; }
        else out += static_cast<char>(c);
    }
    return out + '"';
}
const std::string& Get(const Arguments& args, const std::string& name) {
    const auto found = args.find(name);
    if (found == args.end()) throw std::invalid_argument("missing argument: " + name);
    return found->second;
}
template<class T> T Number(const std::string& text) {
    if (text.empty() || text.find_first_of(" \t\r\n") != std::string::npos)
        throw std::invalid_argument("invalid numeric argument");
    std::istringstream input(text); input.imbue(std::locale::classic());
    T value{};
    if (!(input >> value) || !input.eof()) throw std::invalid_argument("invalid numeric argument");
    return value;
}
void Absolute(const std::string& value) {
    if (value.empty() || value[0] != '/') throw std::invalid_argument("absolute path required");
}
PreparedOrder Order(const Arguments& args) {
    InstrumentRef contract;
    contract.symbol = Get(args, "--symbol"); contract.secType = Get(args, "--sec-type");
    contract.exchange = Get(args, "--exchange"); contract.currency = Get(args, "--currency");
    if (contract.secType != "STK" && contract.secType != "CASH")
        throw std::invalid_argument("application profile supports STK/CASH only");
    const auto quantity = Number<double>(Get(args, "--quantity"));
    const auto price = Number<double>(Get(args, "--limit-price"));
    const auto reference = Number<double>(Get(args, "--reference-price"));
    if (!std::isfinite(quantity) || !std::isfinite(price) || !std::isfinite(reference) ||
        quantity > 1e12 || price > 1e12 || reference > 1e12)
        throw std::invalid_argument("application numeric bound");
    return PreparedOrder(Get(args, "--instrument"), contract, Get(args, "--side"),
        quantity, price, reference, Number<std::int64_t>(Get(args, "--expires-at-ms")));
}
int Emit(const std::string& operation, bool ok, const std::string& id,
         const std::string& binding, const std::string& reason, const std::string& result) {
    std::cout << "{\"schema\":\"hepta.strategy-client-cli.v1\",\"operation\":" << Quoted(operation)
              << ",\"ok\":" << (ok ? "true" : "false") << ",\"command_id\":" << Quoted(id)
              << ",\"binding\":" << Quoted(binding) << ",\"reason\":" << Quoted(reason)
              << ",\"result\":" << (result.empty() ? "null" : result) << "}\n";
    std::cout.flush();
    return std::cout ? (ok ? 0 : 2) : 3;
}
}
int main(int argc, char** argv) {
    std::string operation, id, binding;
    try {
        if (argc < 2 || argc > 48 || argc % 2 != 0)
            throw std::invalid_argument("operation followed by --name value pairs required");
        operation = argv[1];
        if (operation != "binding" && operation != "prepare" && operation != "validate" &&
            operation != "submit" && operation != "inspect")
            throw std::invalid_argument("unsupported operation");
        std::set<std::string> required{"--socket", "--token-file", "--timeout-ms"};
        if (operation != "binding") {
            for (const char* name : {"--directory", "--binding", "--instrument", "--symbol",
                 "--sec-type", "--exchange", "--currency", "--side", "--quantity", "--limit-price",
                 "--reference-price", "--expires-at-ms"}) required.insert(name);
            required.insert(operation == "prepare" ? "--call-id" : "--command-id");
            if (operation == "inspect") required.insert("--call-id");
        }
        Arguments args;
        for (int n = 2; n < argc; n += 2) {
            const std::string key(argv[n]), value(argv[n + 1]);
            if (!required.count(key) || value.empty() || value.size() > 4096 ||
                !args.emplace(key, value).second) throw std::invalid_argument("unknown, duplicate or invalid argument");
        }
        if (args.size() != required.size()) throw std::invalid_argument("missing argument");
        NativeToolClientConfig config;
        config.socketPath = Get(args, "--socket"); config.tokenFile = Get(args, "--token-file");
        Absolute(config.socketPath); Absolute(config.tokenFile);
        config.timeoutMs = Number<int>(Get(args, "--timeout-ms"));
        if (config.timeoutMs < 1 || config.timeoutMs > 120000) throw std::invalid_argument("timeout bound");
        NativeToolClient native(config); NativeStrategyClient client(native);
        std::string reason;
        if (!native.RecoveryBinding(binding, reason)) return Emit(operation, false, id, "", reason, "");
        if (operation == "binding") return Emit(operation, true, "", binding, "", "");
        if (binding != Get(args, "--binding"))
            return Emit(operation, false, "", "", "NATIVE_RECOVERY_BINDING_MISMATCH", "");
        const std::string directory = Get(args, "--directory"); Absolute(directory);
        const PreparedOrder order = Order(args);
        PreparedStrategyCommand prepared; NativeToolClientResult result;
        if (operation == "prepare") {
            if (!client.Prepare(order, Get(args, "--call-id"), prepared, result, reason))
                return Emit(operation, false, prepared.CommandId(), binding, reason, result.responseJson);
            id = prepared.CommandId();
            if (!client.Persist(directory, prepared, reason)) return Emit(operation, false, id, binding, reason, "");
        } else {
            id = Get(args, "--command-id");
            if (!client.Restore(directory, id, prepared, reason)) return Emit(operation, false, id, binding, reason, "");
        }
        if (!client.MatchesOrder(prepared, order, binding, reason))
            return Emit(operation, false, id, binding, reason, "");
        if (operation == "prepare" || operation == "validate") return Emit(operation, true, id, binding, "", "");
        const bool transported = operation == "submit" ? client.Submit(prepared, result, reason) :
            client.Inspect(prepared, Get(args, "--call-id"), result, reason);
        // ok describes transport only. Rejected/unknown/uncertain envelopes are
        // returned unchanged; successful transport is never a fill assertion.
        return Emit(operation, transported, id, binding, reason, result.responseJson);
    } catch (const std::exception& error) { return Emit(operation, false, id, binding, error.what(), ""); }
}
