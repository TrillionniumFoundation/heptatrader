#pragma once
#include <cmath>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

inline void Check(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}
inline void Near(double a, double b, double tolerance = 1e-9) {
    if (!std::isfinite(a) || !std::isfinite(b) || std::fabs(a - b) > tolerance)
        throw std::runtime_error("numeric mismatch " + std::to_string(a) + " vs " + std::to_string(b));
}
inline void Throws(const std::function<void()>& f) {
    bool threw = false;
    try { f(); } catch (const std::exception&) { threw = true; }
    Check(threw, "expected rejection");
}
inline int Run(const std::function<void()>& tests) {
    try { tests(); std::cout << "PASS\n"; return 0; }
    catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
