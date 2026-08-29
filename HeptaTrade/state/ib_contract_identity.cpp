#include "ib_contract_identity.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <sstream>

namespace {

std::string NormalizeToken(std::string value)
{
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), [](unsigned char character) {
        return std::isspace(character) == 0;
    }));
    value.erase(std::find_if(value.rbegin(), value.rend(), [](unsigned char character) {
        return std::isspace(character) == 0;
    }).base(), value.end());
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
        return static_cast<char>(std::toupper(character));
    });
    return value;
}

std::string NormalizeSimpleKey(std::string value)
{
    value.erase(std::remove_if(value.begin(), value.end(), [](unsigned char character) {
        return std::isspace(character) != 0;
    }), value.end());
    std::replace(value.begin(), value.end(), '/', '.');
    return NormalizeToken(value);
}

std::string NormalizeLocalSymbol(std::string value)
{
    value.erase(std::remove_if(value.begin(), value.end(), [](unsigned char character) {
        return std::isspace(character) != 0;
    }), value.end());
    return NormalizeToken(value);
}

std::string StrikeToken(double strike)
{
    if (!std::isfinite(strike) || strike <= 0.0) return std::string();
    std::ostringstream out;
    out << std::setprecision(15) << strike;
    return out.str();
}

std::string DerivativeIdentity(const IBContractLite& contract, const std::string& securityType)
{
    const std::string localSymbol = NormalizeLocalSymbol(contract.localSymbol);
    const std::string exchange = NormalizeToken(contract.exchange);
    const std::string currency = NormalizeToken(contract.currency);
    if (exchange.empty() || currency.empty()) return std::string();
    if (!localSymbol.empty())
        return securityType + ":" + localSymbol + ":" + currency + ":" + exchange;

    const std::string symbol = NormalizeToken(contract.symbol);
    const std::string expiry = NormalizeToken(contract.lastTradeDateOrContractMonth);
    if (symbol.empty() || expiry.empty()) return std::string();

    std::ostringstream out;
    out << securityType << ":" << symbol << ":" << expiry;
    if (securityType == "OPT" || securityType == "FOP")
    {
        std::string right = NormalizeToken(contract.right);
        if (right == "CALL") right = "C";
        if (right == "PUT") right = "P";
        const std::string strike = StrikeToken(contract.strike);
        const std::string multiplier = NormalizeToken(contract.multiplier);
        if ((right != "C" && right != "P") || strike.empty() || multiplier.empty())
            return std::string();
        out << ":" << right << ":" << strike << ":" << multiplier;
    }
    out << ":" << NormalizeToken(contract.tradingClass)
        << ":" << currency << ":" << exchange;
    return out.str();
}

}

std::string BuildIBAuthoritativeInstrumentIdentity(const IBContractLite& contract,
                                                   const std::string& fallbackKey)
{
    const std::string securityType = NormalizeToken(contract.secType);
    const std::string symbol = NormalizeToken(contract.symbol);
    const std::string currency = NormalizeToken(contract.currency);
    if (securityType == "CASH" && !symbol.empty() && !currency.empty())
        return symbol + "." + currency;
    if (securityType == "OPT" || securityType == "FOP" || securityType == "FUT")
        return DerivativeIdentity(contract, securityType);
    if (!symbol.empty()) return symbol;
    return NormalizeSimpleKey(fallbackKey);
}
