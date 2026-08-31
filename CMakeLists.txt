cmake_minimum_required(VERSION 3.21)
project(HeptaTrader LANGUAGES CXX)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    # The legacy pressure fixture is retained for broad protocol coverage, but
    # its one special callback delay is now controlled by an observable
    # UnixToolServer health barrier rather than socket-connect timing.  The
    # preincluded wrapper exposes only this test instance to the interposer;
    # production objects and production scheduling semantics are unchanged.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        set_property(TARGET hepta_unix_tool_server_tests
            PROPERTY INTERPROCEDURAL_OPTIMIZATION FALSE)
        target_compile_options(hepta_unix_tool_server_tests PRIVATE
            "SHELL:-include ${CMAKE_SOURCE_DIR}/tests/unix_tool_server_test_hooks.h")
    endif()

    # A focused fixture proves owner-queue saturation and cross-owner progress
    # with an explicit callback barrier.  It is separate from the broad socket
    # protocol fixture so a timing regression cannot hide behind unrelated
    # assertions.
    if(UNIX)
        add_executable(hepta_unix_tool_server_owner_backpressure_tests
            tests/unix_tool_server_owner_backpressure_tests.cpp)
        target_include_directories(
            hepta_unix_tool_server_owner_backpressure_tests PRIVATE
            HeptaTrade HeptaTrade/adapter_ib)
        target_link_libraries(
            hepta_unix_tool_server_owner_backpressure_tests PRIVATE
            hepta_agent_os_core
            hepta_execution_core
            hepta_native_tool_client
            pthread)
        set_target_properties(
            hepta_unix_tool_server_owner_backpressure_tests PROPERTIES
            CXX_STANDARD 17
            CXX_STANDARD_REQUIRED ON
            CXX_EXTENSIONS OFF)
        if(MSVC)
            target_compile_options(
                hepta_unix_tool_server_owner_backpressure_tests PRIVATE
                /UNDEBUG)
        else()
            target_compile_options(
                hepta_unix_tool_server_owner_backpressure_tests PRIVATE
                -UNDEBUG)
        endif()
        add_test(
            NAME hepta_unix_tool_server_owner_backpressure_tests
            COMMAND hepta_unix_tool_server_owner_backpressure_tests)
        set_tests_properties(
            hepta_unix_tool_server_owner_backpressure_tests PROPERTIES
            LABELS "core"
            RUN_SERIAL TRUE
            TIMEOUT 30)
        if(TARGET hepta_core_test_binaries)
            add_dependencies(
                hepta_core_test_binaries
                hepta_unix_tool_server_owner_backpressure_tests)
        endif()
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
