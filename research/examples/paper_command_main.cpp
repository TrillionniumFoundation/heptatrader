// Thin operator client; all order identity, HSR1 storage and wire calls remain
// in NativeStrategyClient. There is no Broker SDK or autonomous loop here.
#include <hepta/research/native_strategy_client.h>
#include <cmath>
#include <iostream>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
using namespace hepta::research;
namespace {
std::string quote(const std::string& value) {
    const char* hex = "0123456789abcdef";
    std::string out = "\"";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32 || c >= 127) {
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += static_cast<char>(c);
    }
    return out + '"';
}
template<class T> T number(const std::string& text) {
    if (text.empty() || text.find_first_of(" \r\n\t") != std::string::npos)
        throw std::invalid_argument("invalid number");
    std::istringstream in(text); in.imbue(std::locale::classic()); T n{};
    if (!(in >> n) || !in.eof()) throw std::invalid_argument("invalid number");
    return n;
}
int emit(bool operationOk, const std::string& id, const std::string& reason,
         const NativeToolClientResult& result, bool applicationAware = false) {
    const bool transportOk = !result.responseJson.empty();
    const std::string status = result.envelope.status;
    const bool applicationAccepted = status == "ok" || status == "duplicate";
    const bool accepted = applicationAware ? (operationOk && applicationAccepted) : operationOk;
    const std::string outcome = !applicationAware || !transportOk ? "none" :
        applicationAccepted ? "accepted" : status == "uncertain" ? "uncertain" : "rejected";
    std::cout << "{\"ok\":" << (accepted ? "true" : "false")
              << ",\"transport_ok\":" << (transportOk ? "true" : "false")
              << ",\"application_outcome\":" << quote(outcome)
              << ",\"application_status\":" << quote(status)
              << ",\"application_reason\":" << quote(result.envelope.reasonCode)
              << ",\"application_order_id\":" << result.envelope.orderId
              << ",\"command_id\":" << quote(id) << ",\"reason\":" << quote(reason)
              << ",\"result\":" << (result.responseJson.empty() ? "null" : result.responseJson) << "}\n";
    return std::cout ? (accepted ? 0 : 2) : 74;
}
}
int main(int argc, char** argv) {
    std::string id, reason; NativeToolClientResult result;
    try {
        if (argc < 2 || argc > 32 || argc % 2 != 0)
            throw std::invalid_argument("operation and named argument pairs required");
        const std::string op(argv[1]);
        std::set<std::string> required{"--socket", "--token-file", "--directory"};
        if (op == "prepare-open") {
            for (const auto* name : {"--call-id", "--side", "--price", "--expiry"}) required.insert(name);
        } else if (op == "prepare-crypto-open") {
            for (const auto* name : {"--call-id", "--price", "--expiry", "--cash-quantity"}) required.insert(name);
        } else if (op == "prepare-close" || op == "prepare-crypto-close") required.insert("--call-id");
        else if (op == "submit" || op == "inspect") {
            required.insert("--command-id");
            if (op == "inspect") required.insert("--call-id");
        } else throw std::invalid_argument("unsupported operation");
        std::map<std::string,std::string> args;
        for (int n=2; n<argc; n+=2) {
            if (!required.count(argv[n]) || !argv[n+1][0] || std::string(argv[n+1]).size()>4096 ||
                !args.emplace(argv[n],argv[n+1]).second) throw std::invalid_argument("invalid arguments");
        }
        if (args.size()!=required.size()) throw std::invalid_argument("missing argument");
        for (const auto* path : {"--socket", "--token-file", "--directory"})
            if (args.at(path)[0]!='/') throw std::invalid_argument("absolute paths required");
        NativeToolClientConfig config;
        config.socketPath=args.at("--socket"); config.tokenFile=args.at("--token-file"); config.timeoutMs=8000;
        NativeToolClient native(config); NativeStrategyClient client(native); PreparedStrategyCommand prepared;
        if (op == "prepare-open") {
            InstrumentRef c; c.symbol="EUR"; c.secType="CASH"; c.exchange="IDEALPRO"; c.currency="USD";
            const double price=number<double>(args.at("--price"));
            const auto order=PreparedOrder::MarketCash("EUR.USD",c,args.at("--side"),25000.0,price,
                number<std::int64_t>(args.at("--expiry")));
            if (!client.Prepare(order,args.at("--call-id"),prepared,result,reason))
                return emit(false,prepared.CommandId(),reason,result);
        } else if (op == "prepare-crypto-open") {
            const auto order = PreparedOrder::MarketCrypto(number<double>(args.at("--cash-quantity")),
                number<double>(args.at("--price")), number<std::int64_t>(args.at("--expiry")));
            if (!client.Prepare(order,args.at("--call-id"),prepared,result,reason))
                return emit(false,prepared.CommandId(),reason,result);
        } else if (op == "prepare-close" || op == "prepare-crypto-close") {
            if (!client.Prepare(PreparedFlatten(op == "prepare-close" ? "EUR.USD" : "BTC.USD"),args.at("--call-id"),prepared,result,reason))
                return emit(false,prepared.CommandId(),reason,result);
        } else {
            id=args.at("--command-id");
            if (!client.Restore(args.at("--directory"),id,prepared,reason)) return emit(false,id,reason,result);
            if (prepared.ToolName()!="trade.place_order" && prepared.ToolName()!="trade.flatten_position")
                return emit(false,id,"PAPER_TEST_TOOL_NOT_ALLOWED",result);
            const bool ok=op=="submit" ? client.Submit(prepared,result,reason) :
                client.Inspect(prepared,args.at("--call-id"),result,reason);
            return emit(ok,id,reason,result,true);
        }
        id=prepared.CommandId();
        const bool saved=client.Persist(args.at("--directory"),prepared,reason);
        // Do not leak preview permits in the process response or daemon logs.
        return emit(saved,id,reason,NativeToolClientResult());
    } catch (const std::exception& error) { return emit(false,id,error.what(),NativeToolClientResult()); }
}
