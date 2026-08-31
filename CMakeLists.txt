cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # Hold only the first owner-pressure callback inside this test target. The
    # compile-time usleep alias is immune to mixed LTO/static-library linker
    # behavior and leaves all production objects and runtime symbols intact.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        set_property(TARGET hepta_unix_tool_server_tests
            PROPERTY INTERPROCEDURAL_OPTIMIZATION FALSE)
        target_sources(hepta_unix_tool_server_tests PRIVATE
            tests/unix_tool_server_test_interposer.cpp)
        target_compile_definitions(hepta_unix_tool_server_tests PRIVATE
            usleep=hepta_test_usleep)
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
