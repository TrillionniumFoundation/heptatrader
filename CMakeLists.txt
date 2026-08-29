cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(BUILD_IB_PROBE "Build the IB/TWS connectivity probe" OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue composition binary" OFF)
option(HEPTA_BUILD_LEGACY_SIMULATOR
       "Build the deprecated Pegasus simulator" OFF)
option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

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

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
