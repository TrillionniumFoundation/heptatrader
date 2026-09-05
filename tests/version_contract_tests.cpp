#include "heptatrader_version.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "version contract failed at line " << line << ": "
              << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)
}

int main(int argc, char** argv)
{
    REQUIRE(argc == 2);
    const std::string configured = HEPTA_VERSION_FULL;
    const std::string expected = argv[1];
    REQUIRE(!configured.empty());
    REQUIRE(configured == expected);
    REQUIRE(std::string(HEPTA_VERSION_CORE) ==
            std::to_string(HEPTA_VERSION_MAJOR) + "." +
            std::to_string(HEPTA_VERSION_MINOR) + "." +
            std::to_string(HEPTA_VERSION_PATCH));
    REQUIRE(configured.compare(0, std::string(HEPTA_VERSION_CORE).size(),
                               HEPTA_VERSION_CORE) == 0);
    return 0;
}
