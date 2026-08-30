cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # Extend only the first owner-pressure callback in this test executable.
    # The test-only linker wrapper gives all concurrent clients a deterministic
    # overlap window without changing UnixToolServer production queue limits.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        target_sources(hepta_unix_tool_server_tests PRIVATE
            tests/unix_tool_server_test_interposer.cpp)
        target_link_options(hepta_unix_tool_server_tests PRIVATE
            "-Wl,--wrap=usleep")
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
