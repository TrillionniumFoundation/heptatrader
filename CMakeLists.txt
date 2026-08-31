cmake_minimum_required(VERSION 3.16)
project(HeptaTrader LANGUAGES CXX)

file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" HEPTA_VERSION
     LIMIT_COUNT 1 ENCODING UTF-8)
string(STRIP "${HEPTA_VERSION}" HEPTA_VERSION)
if(HEPTA_VERSION STREQUAL "")
    message(FATAL_ERROR "VERSION must contain a non-empty release identifier")
endif()

option(BUILD_IB_PROBE "Build the IB/TWS connectivity probe" OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue composition binary" OFF)
option(HEPTA_BUILD_LEGACY_SIMULATOR
       "Build the deprecated Pegasus simulator" OFF)

include(CTest)
include(cmake/HeptaProjectOptions.cmake)

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

include(cmake/HeptaTargetHardening.cmake)
include(cmake/HeptaInstall.cmake)
