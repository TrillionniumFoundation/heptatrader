cmake_minimum_required(VERSION 3.21)

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" HEPTA_VERSION_FULL)
string(STRIP "${HEPTA_VERSION_FULL}" HEPTA_VERSION_FULL)
if(NOT HEPTA_VERSION_FULL MATCHES
   "^[0-9]+\\.[0-9]+\\.[0-9]+([-+][A-Za-z0-9.-]+)?$")
    message(FATAL_ERROR "VERSION is not valid semantic version text: ${HEPTA_VERSION_FULL}")
endif()
string(REGEX MATCH "^[0-9]+\\.[0-9]+\\.[0-9]+"
       HEPTA_VERSION_CORE "${HEPTA_VERSION_FULL}")

project(HeptaTrader VERSION "${HEPTA_VERSION_CORE}" LANGUAGES CXX)
set(HEPTA_VERSION_FULL "${HEPTA_VERSION_FULL}" CACHE INTERNAL
    "Canonical full HeptaTrader semantic version")
set(HEPTA_GENERATED_INCLUDE_DIR
    "${CMAKE_CURRENT_BINARY_DIR}/generated/include" CACHE INTERNAL
    "Generated HeptaTrader public build-contract headers")
file(MAKE_DIRECTORY "${HEPTA_GENERATED_INCLUDE_DIR}")
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/heptatrader_version.h.in"
    "${HEPTA_GENERATED_INCLUDE_DIR}/heptatrader_version.h"
    @ONLY)

option(HEPTA_INSTALL_RUNTIME
       "Define the minimal runtime install component" ON)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(HeptaTrade)

if(BUILD_TESTING)
    add_subdirectory(tests)

    add_executable(hepta_version_contract_tests
        tests/version_contract_tests.cpp)
    target_include_directories(hepta_version_contract_tests PRIVATE
        "${HEPTA_GENERATED_INCLUDE_DIR}")
    set_target_properties(hepta_version_contract_tests PROPERTIES
        CXX_STANDARD 17
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    if(MSVC)
        target_compile_options(hepta_version_contract_tests PRIVATE /UNDEBUG)
    else()
        target_compile_options(hepta_version_contract_tests PRIVATE -UNDEBUG)
    endif()
    add_test(NAME hepta_version_contract_tests
             COMMAND hepta_version_contract_tests "${HEPTA_VERSION_FULL}")
    set_tests_properties(hepta_version_contract_tests PROPERTIES
        LABELS "core"
        TIMEOUT 10)
    if(TARGET hepta_core_test_binaries)
        add_dependencies(hepta_core_test_binaries hepta_version_contract_tests)
    endif()

    # The legacy pressure fixture is retained for broad protocol coverage, but
    # its one special callback delay is now controlled by an observable
    # UnixToolServer health barrier rather than socket-connect timing. The
    # preincluded wrapper exposes only this test instance to the interposer;
    # production objects and production scheduling semantics are unchanged.
    if(TARGET hepta_unix_tool_server_tests AND UNIX AND NOT APPLE)
        set_property(TARGET hepta_unix_tool_server_tests
            PROPERTY INTERPROCEDURAL_OPTIMIZATION FALSE)
        target_compile_options(hepta_unix_tool_server_tests PRIVATE
            "SHELL:-include ${CMAKE_SOURCE_DIR}/tests/unix_tool_server_test_hooks.h")
    endif()

    # A focused fixture proves owner-queue saturation and cross-owner progress
    # with an explicit callback barrier. It is separate from the broad socket
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

    # This end-to-end fixture proves that an incomplete or malformed
    # authoritative position collection cannot be promoted into either a
    # decision snapshot or a target-position preview permit.
    add_executable(hepta_target_position_malformed_snapshot_tests
        tests/target_position_malformed_snapshot_tests.cpp)
    target_include_directories(
        hepta_target_position_malformed_snapshot_tests PRIVATE
        HeptaTrade HeptaTrade/adapter_ib)
    target_link_libraries(
        hepta_target_position_malformed_snapshot_tests PRIVATE
        hepta_trading_tool_core)
    set_target_properties(
        hepta_target_position_malformed_snapshot_tests PROPERTIES
        CXX_STANDARD 17
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    if(MSVC)
        target_compile_options(
            hepta_target_position_malformed_snapshot_tests PRIVATE
            /UNDEBUG)
    else()
        target_compile_options(
            hepta_target_position_malformed_snapshot_tests PRIVATE
            -UNDEBUG)
    endif()
    add_test(
        NAME hepta_target_position_malformed_snapshot_tests
        COMMAND hepta_target_position_malformed_snapshot_tests)
    set_tests_properties(
        hepta_target_position_malformed_snapshot_tests PROPERTIES
        LABELS "core"
        TIMEOUT 30)
    if(TARGET hepta_core_test_binaries)
        add_dependencies(
            hepta_core_test_binaries
            hepta_target_position_malformed_snapshot_tests)
    endif()
endif()

if(HEPTA_INSTALL_RUNTIME)
    include(cmake/RuntimeInstall.cmake)
endif()
