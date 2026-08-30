cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # Force-include a source-level synchronization barrier only in the Unix
    # tool-server test target. It tracks the test's client sockets and makes
    # the owner-queue saturation assertion deterministic without changing
    # production UnixToolServer scheduling, limits or symbols.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        target_compile_options(hepta_unix_tool_server_tests PRIVATE
            "SHELL:-include ${CMAKE_CURRENT_SOURCE_DIR}/tests/unix_tool_server_pressure_barrier.h")
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
