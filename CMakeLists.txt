cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # Synchronize the owner-pressure fixture through symbols compiled directly
    # into the test target.  Compile-time renaming avoids linker --wrap/LTO
    # ambiguity and leaves every production object and runtime symbol intact.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        set_property(TARGET hepta_unix_tool_server_tests
            PROPERTY INTERPROCEDURAL_OPTIMIZATION FALSE)
        target_sources(hepta_unix_tool_server_tests PRIVATE
            tests/unix_tool_server_test_interposer.cpp)
        target_compile_definitions(hepta_unix_tool_server_tests PRIVATE
            connect=hepta_test_connect
            close=hepta_test_close
            usleep=hepta_test_usleep)
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
