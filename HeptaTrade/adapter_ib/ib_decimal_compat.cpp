#if defined(__has_include)
#  if __has_include("Decimal.h")
#    include "Decimal.h"
#  else
using Decimal = long long;
#  endif
#else
using Decimal = long long;
#endif

#include <algorithm>
#include <cstring>
#include <limits>
#include <cstdlib>
#include <cmath>
#include <cstdio>
#include <locale>
#include <sstream>

namespace {
inline double DecimalToDouble(Decimal v) {
    double d = 0.0;
    std::memcpy(&d, &v, sizeof(d));
    return d;
}
inline Decimal DoubleToDecimal(double d) {
    Decimal v = 0;
    std::memcpy(&v, &d, std::min(sizeof(v), sizeof(d)));
    return v;
}
}

extern "C" Decimal __bid64_add(Decimal a, Decimal b, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    return DoubleToDecimal(DecimalToDouble(a) + DecimalToDouble(b));
}

extern "C" Decimal __bid64_sub(Decimal a, Decimal b, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    return DoubleToDecimal(DecimalToDouble(a) - DecimalToDouble(b));
}

extern "C" Decimal __bid64_mul(Decimal a, Decimal b, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    return DoubleToDecimal(DecimalToDouble(a) * DecimalToDouble(b));
}

extern "C" Decimal __bid64_div(Decimal a, Decimal b, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    return DoubleToDecimal(DecimalToDouble(a) / DecimalToDouble(b));
}

extern "C" Decimal __bid64_from_string(char* cstr, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    if (!cstr) return 0;
    try {
        // IB's decimal text is dot-decimal protocol data.  Do not let the
        // hosting process's C locale reinterpret it (or accept a comma
        // prefix and silently truncate the value).
        std::istringstream input(cstr);
        input.imbue(std::locale::classic());
        input >> std::noskipws;
        double value = 0.0;
        input >> value;
        if (!input || !input.eof() || !std::isfinite(value)) {
            if (pflags) *pflags = 1;
            return 0;
        }
        return DoubleToDecimal(value);
    } catch (...) {
        if (pflags) *pflags = 1;
        return 0;
    }
}

extern "C" void __bid64_to_string(char* out, Decimal value, unsigned int*) {
    if (!out) return;
    const double d = DecimalToDouble(value);
    if (!std::isfinite(d)) {
        std::strcpy(out, "0");
        return;
    }
    std::snprintf(out, 64, "%.*g", std::numeric_limits<double>::digits10, d);
}

extern "C" double __bid64_to_binary64(Decimal value, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    return DecimalToDouble(value);
}

extern "C" Decimal __binary64_to_bid64(double d, unsigned int, unsigned int* pflags) {
    if (pflags) *pflags = 0;
    return DoubleToDecimal(d);
}
