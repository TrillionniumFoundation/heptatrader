cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # The owner-pressure fixture tracks only client-side Unix socket connects.
    # Its first pressure callback waits until all four clients are connected,
    # then keeps one request active long enough to saturate the configured
    # two-entry owner queue. These linker wrappers are test-only; production
    # UnixToolServer queue limits and scheduling are unchanged.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        target_sources(hepta_unix_tool_server_tests PRIVATE
            tests/unix_tool_server_test_interposer.cpp)
        target_link_options(hepta_unix_tool_server_tests PRIVATE
            "-Wl,--wrap=connect"
            "-Wl,--wrap=close"
            "-Wl,--wrap=usleep")
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
