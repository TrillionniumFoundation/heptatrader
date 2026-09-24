cmake_minimum_required(VERSION 3.16)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(HeptaResearch LANGUAGES CXX)
    include(CTest)
    set(HEPTA_RESEARCH_STANDALONE ON)
else()
    set(HEPTA_RESEARCH_STANDALONE OFF)
    if(NOT TARGET hepta_native_tool_client)
        message(FATAL_ERROR "Research integration requires the canonical NativeToolClient target")
    endif()
endif()
# Explicit C++11 on every target; the standalone compiler's default must not
# silently change the SDK contract or the reviewed root build inventory.
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(hepta_research_data STATIC src/market_data.cpp)
add_library(hepta_research_analytics STATIC src/analytics.cpp)
add_library(hepta_research_replay STATIC src/replay.cpp)
add_library(hepta_research_strategy STATIC src/strategy.cpp)
foreach(target hepta_research_data hepta_research_analytics hepta_research_replay hepta_research_strategy)
    target_include_directories(${target} PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
    target_compile_features(${target} PUBLIC cxx_std_11)
    set_target_properties(${target} PROPERTIES CXX_EXTENSIONS OFF)
endforeach()
target_link_libraries(hepta_research_analytics PUBLIC hepta_research_data)
target_link_libraries(hepta_research_replay PUBLIC hepta_research_analytics hepta_research_data)
target_link_libraries(hepta_research_strategy PUBLIC hepta_research_data)

# Native transport is deliberately a separate link closure. Offline users do
# not inherit any gateway, socket, OpenSSL, journal, or vendor SDK dependency.
if(NOT HEPTA_RESEARCH_STANDALONE)
    add_library(hepta_research_native_client STATIC src/native_strategy_client.cpp)
    target_include_directories(hepta_research_native_client PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
    target_compile_features(hepta_research_native_client PUBLIC cxx_std_11)
    target_link_libraries(hepta_research_native_client PUBLIC hepta_native_tool_client)
    add_executable(hepta_strategy_client_cli examples/strategy_client_main.cpp)
    set_target_properties(hepta_strategy_client_cli PROPERTIES OUTPUT_NAME "hepta-strategy-native")
    target_compile_features(hepta_strategy_client_cli PRIVATE cxx_std_11)
    target_link_libraries(hepta_strategy_client_cli PRIVATE hepta_research_native_client)
endif()

add_executable(hepta_research_replay_cli examples/replay_main.cpp)
set_target_properties(hepta_research_replay_cli PROPERTIES OUTPUT_NAME "hepta-research-replay")
target_link_libraries(hepta_research_replay_cli PRIVATE hepta_research_replay hepta_research_strategy)

if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_custom_target(hepta_research_test_binaries)
    function(hepta_research_test target source)
        add_executable(${target} ${source})
        target_compile_features(${target} PRIVATE cxx_std_11)
        add_test(NAME ${target} COMMAND ${target} ${ARGN})
        set_tests_properties(${target} PROPERTIES LABELS "core;research" TIMEOUT 30)
        add_dependencies(hepta_research_test_binaries ${target})
    endfunction()
    hepta_research_test(hepta_research_market_data_tests ../tests/research/market_data_tests.cpp)
    target_link_libraries(hepta_research_market_data_tests PRIVATE hepta_research_data)
    hepta_research_test(hepta_research_analytics_tests ../tests/research/analytics_tests.cpp)
    target_link_libraries(hepta_research_analytics_tests PRIVATE hepta_research_analytics)
    hepta_research_test(hepta_research_replay_tests ../tests/research/replay_tests.cpp)
    target_link_libraries(hepta_research_replay_tests PRIVATE hepta_research_replay hepta_research_strategy)
    if(NOT HEPTA_RESEARCH_STANDALONE)
        hepta_research_test(hepta_research_native_client_tests ../tests/research/native_client_tests.cpp)
        target_link_libraries(hepta_research_native_client_tests PRIVATE hepta_research_native_client)
        hepta_research_test(hepta_research_native_gateway_tests ../tests/research/native_gateway_tests.cpp)
        target_link_libraries(hepta_research_native_gateway_tests PRIVATE
            hepta_research_native_client hepta_agent_os_core)
        hepta_research_test(hepta_research_native_execution_tests
            ../tests/research/native_execution_tests.cpp --lifecycle-only)
        target_link_libraries(hepta_research_native_execution_tests PRIVATE
            hepta_research_native_client hepta_agent_os_core hepta_execution_service_runtime)
        add_dependencies(hepta_research_native_execution_tests hepta_strategy_client_cli)
        # One real binary owns all paths, but CTest records behavior separately.
        # The shared resource lock prevents independent fsync-heavy fixtures from
        # manufacturing preview expiry through test-runner contention.
        set(_hepta_native_execution_lock hepta_native_execution_runtime)
        set_tests_properties(hepta_research_native_execution_tests PROPERTIES
            TIMEOUT 90 RESOURCE_LOCK ${_hepta_native_execution_lock})
        add_test(NAME hepta_research_native_execution_process_crash_tests
            COMMAND hepta_research_native_execution_tests --process-crashes-only)
        add_test(NAME hepta_research_native_execution_durable_raw_tests
            COMMAND hepta_research_native_execution_tests --durable-raw-only)
        add_test(NAME hepta_research_native_execution_durable_prepared_tests
            COMMAND hepta_research_native_execution_tests --durable-prepared-only)
        add_test(NAME hepta_research_native_execution_application_tests
            COMMAND hepta_research_native_execution_tests --application-only
            "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/application_execution_driver.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/strategy_gateway.py"
            "$<TARGET_FILE:hepta_strategy_client_cli>")
        add_test(NAME hepta_research_native_execution_latency_tests
            COMMAND hepta_research_native_execution_tests --latency-only)
        foreach(_hepta_native_execution_test
                hepta_research_native_execution_process_crash_tests
                hepta_research_native_execution_durable_raw_tests
                hepta_research_native_execution_durable_prepared_tests
                hepta_research_native_execution_application_tests
                hepta_research_native_execution_latency_tests)
            set_tests_properties(${_hepta_native_execution_test} PROPERTIES
                LABELS "core;research" TIMEOUT 90
                RESOURCE_LOCK ${_hepta_native_execution_lock})
        endforeach()
        add_test(NAME hepta_research_application_policy COMMAND "${Python3_EXECUTABLE}" -I -B
            "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/strategy_gateway_behavior.py"
            --adapter "${CMAKE_CURRENT_SOURCE_DIR}/strategy_gateway.py"
            --native "$<TARGET_FILE:hepta_strategy_client_cli>")
        set_tests_properties(hepta_research_application_policy PROPERTIES LABELS "core;research" TIMEOUT 30)
    endif()
    add_test(NAME hepta_research_replay_example COMMAND hepta_research_replay_cli
        "${CMAKE_CURRENT_SOURCE_DIR}/examples/ticks.csv"
        "${CMAKE_CURRENT_SOURCE_DIR}/examples/sessions.csv" TEST.FUT 10 1 2 1)
    set_tests_properties(hepta_research_replay_example PROPERTIES LABELS "core;research" TIMEOUT 30
        PASS_REGULAR_EXPRESSION "\"broker_authorized\":false")
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME hepta_research_cli_behavior COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/cli_behavior.py"
        "$<TARGET_FILE:hepta_research_replay_cli>" "${CMAKE_CURRENT_SOURCE_DIR}/examples")
    set_tests_properties(hepta_research_cli_behavior PROPERTIES LABELS "core;research" TIMEOUT 30)
    # The fail-closed file capture/publish helper requires POSIX filesystem calls.
    if(UNIX)
        add_test(NAME hepta_research_legacy_bundle_behavior COMMAND "${Python3_EXECUTABLE}" -I -S
            "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/legacy_bundle_behavior.py"
            --importer "${CMAKE_CURRENT_SOURCE_DIR}/import_legacy.py"
            --native "$<TARGET_FILE:hepta_research_replay_cli>")
        set_tests_properties(hepta_research_legacy_bundle_behavior PROPERTIES LABELS "core;research" TIMEOUT 120)
    endif()
    set(_model_manifest_args)
    if(UNIX)
        list(APPEND _model_manifest_args "--manifest-adapter=${CMAKE_CURRENT_SOURCE_DIR}/model_manifest.py")
    endif()
    add_test(NAME hepta_research_model_cli COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/model_cli_behavior.py"
        "--binary=$<TARGET_FILE:hepta_research_replay_cli>"
        ${_model_manifest_args})
    set_tests_properties(hepta_research_model_cli PROPERTIES LABELS "core;research" TIMEOUT 60)
    if(HEPTA_RESEARCH_STANDALONE)
        add_test(NAME hepta_research_model_cli_install COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/model_cli_behavior.py"
            "--build-dir=${CMAKE_CURRENT_BINARY_DIR}" "--cmake=${CMAKE_COMMAND}" "--config=$<CONFIG>")
        set_tests_properties(hepta_research_model_cli_install PROPERTIES LABELS "research;install" TIMEOUT 90)
    endif()
    add_dependencies(hepta_research_test_binaries hepta_research_replay_cli)
    if(TARGET hepta_core_test_binaries)
        add_dependencies(hepta_core_test_binaries hepta_research_test_binaries)
    endif()
endif()

# Standalone developer packaging must never extend the production install set.
if(HEPTA_RESEARCH_STANDALONE)
    include(cmake/HeptaResearchInstall.cmake)
else()
    include("${PROJECT_SOURCE_DIR}/cmake/HeptaStrategyClientSDK.cmake")
endif()
