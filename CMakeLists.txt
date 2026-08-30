cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # Synchronize only the owner-pressure fixture on the four actual request
    # bodies written by its clients. Compile the framing translation unit
    # directly into this target with IPO disabled so the test-only send/usleep
    # wrappers observe the real wire path. Production UnixToolServer code,
    # scheduling and limits remain unchanged.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        set_property(TARGET hepta_unix_tool_server_tests
            PROPERTY INTERPROCEDURAL_OPTIMIZATION FALSE)
        target_sources(hepta_unix_tool_server_tests PRIVATE
            tests/unix_tool_server_test_interposer.cpp
            HeptaTrade/tool_host/typed_tool_framing.cpp)
        target_link_options(hepta_unix_tool_server_tests PRIVATE
            "-Wl,--wrap=send"
            "-Wl,--wrap=usleep")
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
