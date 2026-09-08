#pragma once

#include <string>

// Broker error descriptions are localized. Only the exact trailing farm id
// is stable. X230's regional Gateway routes the observed CASH quote stream
// through hfarm; cashfarm remains supported. This is only a prerequisite for
// requesting quotes, never a substitute for fresh contract-bound quote data.
inline bool IsSupportedIbCashMarketDataFarm(const std::string& description)
{
    const std::string::size_type separator = description.rfind(':');
    if (separator == std::string::npos) return false;
    std::string farm = description.substr(separator + 1);
    const auto first = farm.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return false;
    farm = farm.substr(first, farm.find_last_not_of(" \t\r\n") - first + 1);
    for (char& character : farm)
        if (character >= 'A' && character <= 'Z') character += 'a' - 'A';
    return farm == "cashfarm" || farm == "hfarm";
}
