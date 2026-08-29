cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)


if(BUILD_TESTING)
    add_subdirectory(tests)
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
