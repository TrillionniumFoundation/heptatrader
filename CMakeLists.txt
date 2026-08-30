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
    # bodies written by its clients. Link wrapping is confined to this test
    # executable and leaves production UnixToolServer scheduling and limits
    # unchanged.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        target_sources(hepta_unix_tool_server_tests PRIVATE
            tests/unix_tool_server_test_interposer.cpp)
        target_link_options(hepta_unix_tool_server_tests PRIVATE
            "-Wl,--wrap=send"
            "-Wl,--wrap=usleep")
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
