cmake_minimum_required(VERSION 3.16)

# Source-tree builds are for iteration and runtime verification only.
# Distribution/install certification belongs outside this repository.
set(CMAKE_SKIP_INSTALL_RULES ON CACHE BOOL
    "Do not generate legacy packaging/install rules" FORCE)

project(HeptaTrader LANGUAGES CXX)

option(BUILD_IB_PROBE "Build the IB/TWS connectivity probe" OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue composition binary" OFF)
option(HEPTA_BUILD_LEGACY_SIMULATOR
       "Build the deprecated Pegasus simulator" OFF)

include(CTest)

if(HEPTA_BUILD_LEGACY_MONOLITH)
    add_subdirectory(HeptaStrategy)
endif()

add_subdirectory(HeptaTrade)

if(HEPTA_BUILD_LEGACY_SIMULATOR)
    add_subdirectory(HeptaSimulator)
endif()

if(BUILD_IB_PROBE)
    add_subdirectory(docs/ib_probe)
endif()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
