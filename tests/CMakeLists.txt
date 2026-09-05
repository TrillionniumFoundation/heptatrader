find_package(OpenSSL REQUIRED)

set(HEPTA_CORE_TEST_TARGETS)
set(HEPTA_RELIABILITY_TEST_TARGETS)

function(hepta_register_reliability_test target)
    target_include_directories(${target} PRIVATE
        ../HeptaTrade
        ../HeptaTrade/adapter_ib)
    set_target_properties(${target} PROPERTIES
        CXX_STANDARD 17
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    if(MSVC)
        target_compile_options(${target} PRIVATE /UNDEBUG)
    else()
        target_compile_options(${target} PRIVATE -UNDEBUG)
    endif()
    add_test(NAME ${target} COMMAND ${target})
    set_tests_properties(${target} PROPERTIES LABELS "reliability")
    list(APPEND HEPTA_RELIABILITY_TEST_TARGETS ${target})
    set(HEPTA_RELIABILITY_TEST_TARGETS
        "${HEPTA_RELIABILITY_TEST_TARGETS}" PARENT_SCOPE)
endfunction()

function(hepta_register_core_test target)
    target_include_directories(${target} PRIVATE
        ../HeptaTrade
        ../HeptaTrade/adapter_ib)
    set_target_properties(${target} PROPERTIES
        CXX_STANDARD 17
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    if(MSVC)
        target_compile_options(${target} PRIVATE /UNDEBUG)
    else()
        target_compile_options(${target} PRIVATE -UNDEBUG)
    endif()
    add_test(NAME ${target} COMMAND ${target})
    set_tests_properties(${target} PROPERTIES LABELS "core")
    list(APPEND HEPTA_CORE_TEST_TARGETS ${target})
    set(HEPTA_CORE_TEST_TARGETS "${HEPTA_CORE_TEST_TARGETS}" PARENT_SCOPE)
endfunction()

function(hepta_load_latency_baseline prefix relative_path expected_fixture)
    set(path "${CMAKE_SOURCE_DIR}/${relative_path}")
    file(READ "${path}" json)
    string(JSON schema ERROR_VARIABLE error GET "${json}" schema)
    if(error OR NOT schema STREQUAL "heptatrader.latency-baseline.v1")
        message(FATAL_ERROR "${relative_path}: invalid latency baseline schema: ${error}")
    endif()
    string(JSON fixture ERROR_VARIABLE error GET "${json}" fixture)
    if(error OR NOT fixture STREQUAL expected_fixture)
        message(FATAL_ERROR "${relative_path}: invalid fixture identity: ${error}")
    endif()
    string(JSON p99 ERROR_VARIABLE error GET "${json}" p99_microseconds)
    if(error OR NOT p99 MATCHES "^[1-9][0-9]*$")
        message(FATAL_ERROR "${relative_path}: invalid p99 baseline: ${error}")
    endif()
    string(JSON regression ERROR_VARIABLE error GET "${json}" maximum_regression_percent)
    if(error OR NOT regression MATCHES "^[0-9]+$" OR regression GREATER 100)
        message(FATAL_ERROR "${relative_path}: invalid regression percentage: ${error}")
    endif()
    set(${prefix}_P99_US "${p99}" PARENT_SCOPE)
    set(${prefix}_REGRESSION_PERCENT "${regression}" PARENT_SCOPE)
endfunction()

add_executable(hepta_trading_contract_tests
    trading_contract_tests.cpp)
hepta_register_core_test(hepta_trading_contract_tests)

add_executable(hepta_native_tool_client_tests
    native_tool_client_tests.cpp)
target_link_libraries(hepta_native_tool_client_tests
    hepta_native_tool_client)
hepta_register_core_test(hepta_native_tool_client_tests)

add_executable(hepta_execution_coordinator_tests
    execution_coordinator_tests.cpp)
target_link_libraries(hepta_execution_coordinator_tests
    hepta_execution_core
    hepta_oms_core)
hepta_register_core_test(hepta_execution_coordinator_tests)

add_executable(hepta_oms_journal_durability_tests
    oms_journal_durability_tests.cpp)
target_link_libraries(hepta_oms_journal_durability_tests
    pthread hepta_observability_core
    hepta_oms_core)
hepta_register_core_test(hepta_oms_journal_durability_tests)

add_executable(hepta_trading_tool_registry_tests
    trading_tool_registry_tests.cpp)
target_link_libraries(hepta_trading_tool_registry_tests
    hepta_trading_tool_core hepta_execution_core)
hepta_register_core_test(hepta_trading_tool_registry_tests)

add_executable(hepta_execution_event_hub_tests
    execution_event_hub_tests.cpp)
target_link_libraries(hepta_execution_event_hub_tests
    hepta_execution_core
    hepta_agent_execution_support)
hepta_register_core_test(hepta_execution_event_hub_tests)

add_executable(hepta_execution_event_feed_tests
    execution_event_feed_tests.cpp)
target_link_libraries(hepta_execution_event_feed_tests
    hepta_execution_core
    hepta_execution_event_relay_core)
hepta_register_core_test(hepta_execution_event_feed_tests)

add_executable(hepta_execution_gateway_runtime_composition_tests
    execution_gateway_runtime_composition_tests.cpp)
target_link_libraries(hepta_execution_gateway_runtime_composition_tests
    hepta_agent_os_core hepta_execution_server)
hepta_register_core_test(hepta_execution_gateway_runtime_composition_tests)

add_executable(hepta_trading_tool_host_tests
    trading_tool_host_tests.cpp)
target_link_libraries(hepta_trading_tool_host_tests
    hepta_agent_os_core hepta_execution_core)
hepta_register_core_test(hepta_trading_tool_host_tests)

add_executable(hepta_unix_tool_server_tests
    unix_tool_server_tests.cpp
    unix_tool_server_test_interposer.cpp)
target_compile_definitions(hepta_unix_tool_server_tests PRIVATE
    connect=hepta_test_connect
    usleep=hepta_test_usleep)
target_link_libraries(hepta_unix_tool_server_tests
    hepta_agent_os_core hepta_execution_core hepta_native_tool_client)
hepta_register_core_test(hepta_unix_tool_server_tests)
set_tests_properties(hepta_unix_tool_server_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_unix_session_supervisor_server_tests
    unix_session_supervisor_server_tests.cpp)
target_link_libraries(hepta_unix_session_supervisor_server_tests
    hepta_agent_os_core hepta_execution_core)
add_dependencies(hepta_unix_session_supervisor_server_tests hepta_sessionctl)
target_compile_definitions(hepta_unix_session_supervisor_server_tests PRIVATE
    HEPTA_SESSIONCTL_TEST_BINARY="$<TARGET_FILE:hepta_sessionctl>")
hepta_register_core_test(hepta_unix_session_supervisor_server_tests)
set_tests_properties(hepta_unix_session_supervisor_server_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_session_supervisor_protocol_boundary_tests
    session_supervisor_protocol_boundary_tests.cpp)
target_link_libraries(hepta_session_supervisor_protocol_boundary_tests
    hepta_agent_os_core)
hepta_register_core_test(hepta_session_supervisor_protocol_boundary_tests)

add_executable(hepta_session_supervisor_lease_store_migration_tests
    session_supervisor_lease_store_migration_tests.cpp)
target_link_libraries(hepta_session_supervisor_lease_store_migration_tests
    hepta_agent_os_core)
hepta_register_core_test(hepta_session_supervisor_lease_store_migration_tests)

add_executable(hepta_agent_simulator_e2e_tests
    agent_simulator_e2e_tests.cpp)
target_link_libraries(hepta_agent_simulator_e2e_tests
    hepta_agent_os_core hepta_execution_core
    hepta_state_core
    hepta_simulator_venue)
hepta_register_core_test(hepta_agent_simulator_e2e_tests)
set_tests_properties(hepta_agent_simulator_e2e_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_decision_lease_manager_tests
    decision_lease_manager_tests.cpp)
target_link_libraries(hepta_decision_lease_manager_tests pthread
    hepta_agent_execution_support)
hepta_register_core_test(hepta_decision_lease_manager_tests)

add_executable(hepta_execution_decision_lease_authority_tests
    execution_decision_lease_authority_tests.cpp)
target_link_libraries(hepta_execution_decision_lease_authority_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_decision_lease_authority_tests)

add_executable(hepta_authoritative_trading_snapshot_store_tests
    authoritative_trading_snapshot_store_tests.cpp)
target_link_libraries(hepta_authoritative_trading_snapshot_store_tests pthread
    hepta_state_core)
hepta_register_core_test(hepta_authoritative_trading_snapshot_store_tests)

add_executable(hepta_snapshot_refresh_coordinator_tests
    snapshot_refresh_coordinator_tests.cpp)
target_link_libraries(hepta_snapshot_refresh_coordinator_tests pthread
    hepta_state_core)
hepta_register_core_test(hepta_snapshot_refresh_coordinator_tests)

add_executable(hepta_ib_order_lifecycle_tests
    ib_order_lifecycle_tests.cpp)
target_link_libraries(hepta_ib_order_lifecycle_tests PRIVATE
    hepta_ib_adapter_core)
hepta_register_core_test(hepta_ib_order_lifecycle_tests)

add_executable(hepta_ib_gateway_adapter_risk_tests
    ib_gateway_adapter_risk_tests.cpp)
target_link_libraries(hepta_ib_gateway_adapter_risk_tests
    hepta_risk_core hepta_observability_core pthread
    hepta_ib_adapter_core)
hepta_register_core_test(hepta_ib_gateway_adapter_risk_tests)

add_executable(hepta_ib_paper_kill_switch_tests
    ib_paper_kill_switch_tests.cpp)
target_link_libraries(hepta_ib_paper_kill_switch_tests pthread
    hepta_execution_core)
hepta_register_core_test(hepta_ib_paper_kill_switch_tests)

add_executable(hepta_deterministic_risk_policy_tests
    deterministic_risk_policy_tests.cpp)
target_link_libraries(hepta_deterministic_risk_policy_tests
    hepta_risk_core)
hepta_register_core_test(hepta_deterministic_risk_policy_tests)

add_executable(hepta_target_position_intent_tests
    target_position_intent_tests.cpp)
target_link_libraries(hepta_target_position_intent_tests OpenSSL::Crypto
    hepta_intent_core)
hepta_register_core_test(hepta_target_position_intent_tests)

add_executable(hepta_authoritative_decision_snapshot_tests
    authoritative_decision_snapshot_tests.cpp)
target_link_libraries(hepta_authoritative_decision_snapshot_tests OpenSSL::Crypto
    hepta_intent_core)
hepta_register_core_test(hepta_authoritative_decision_snapshot_tests)

add_executable(hepta_unsupported_venue_adapter_tests
    unsupported_venue_adapter_tests.cpp)
target_link_libraries(hepta_unsupported_venue_adapter_tests PRIVATE
    hepta_venue_ctp
    hepta_venue_xt)
hepta_register_core_test(hepta_unsupported_venue_adapter_tests)

add_executable(hepta_execution_preview_permit_tests
    execution_preview_permit_tests.cpp)
target_link_libraries(hepta_execution_preview_permit_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_preview_permit_tests)

add_executable(hepta_runtime_telemetry_tests
    runtime_telemetry_tests.cpp)
target_link_libraries(hepta_runtime_telemetry_tests
    hepta_observability_core)
hepta_register_core_test(hepta_runtime_telemetry_tests)

add_executable(hepta_target_position_tool_tests
    target_position_tool_tests.cpp)
target_link_libraries(hepta_target_position_tool_tests
    hepta_trading_tool_core)
hepta_register_core_test(hepta_target_position_tool_tests)

add_executable(hepta_portfolio_compiler_tests
    portfolio_compiler_tests.cpp)
target_link_libraries(hepta_portfolio_compiler_tests
    hepta_portfolio_core)
hepta_register_core_test(hepta_portfolio_compiler_tests)

add_executable(hepta_fixed_decimal_tests
    fixed_decimal_tests.cpp)
target_link_libraries(hepta_fixed_decimal_tests
    hepta_numeric_core)
hepta_register_core_test(hepta_fixed_decimal_tests)

add_executable(hepta_sharded_market_data_tests
    sharded_market_data_tests.cpp)
target_link_libraries(hepta_sharded_market_data_tests
    hepta_marketdata_core)
hepta_register_core_test(hepta_sharded_market_data_tests)

add_executable(hepta_feature_generation_tests
    feature_generation_tests.cpp)
target_link_libraries(hepta_feature_generation_tests
    hepta_feature_runtime)
hepta_register_core_test(hepta_feature_generation_tests)

# Market Data authority is a risk-sensitive concurrency boundary. Keep the
# tests in the fast core lane and also compile/run them under every sanitizer
# reliability matrix so issuer lifecycle, shard synchronization and hostile
# receipt paths cannot regress outside ordinary CTest.
set_tests_properties(
    hepta_sharded_market_data_tests
    hepta_feature_generation_tests
    PROPERTIES LABELS "core;reliability")
list(APPEND HEPTA_RELIABILITY_TEST_TARGETS
    hepta_sharded_market_data_tests
    hepta_feature_generation_tests)

add_executable(hepta_strategy_proposal_tests
    strategy_proposal_tests.cpp)
target_link_libraries(hepta_strategy_proposal_tests
    hepta_proposal_aggregator)
hepta_register_core_test(hepta_strategy_proposal_tests)

add_executable(hepta_global_allocator_tests
    global_allocator_tests.cpp)
target_link_libraries(hepta_global_allocator_tests
    hepta_global_allocator)
hepta_register_core_test(hepta_global_allocator_tests)

add_executable(hepta_allocation_plan_revalidator_tests
    allocation_plan_revalidator_tests.cpp)
target_link_libraries(hepta_allocation_plan_revalidator_tests
    hepta_allocation_revalidator)
hepta_register_core_test(hepta_allocation_plan_revalidator_tests)


add_executable(hepta_module_lifecycle_tests
    module_lifecycle_tests.cpp)
target_link_libraries(hepta_module_lifecycle_tests
    hepta_management_control)
hepta_register_core_test(hepta_module_lifecycle_tests)

add_executable(hepta_multi_agent_allocation_tests
    multi_agent_allocation_tests.cpp)
target_link_libraries(hepta_multi_agent_allocation_tests
    hepta_multi_agent_simulator)
hepta_register_core_test(hepta_multi_agent_allocation_tests)

add_executable(hepta_oms_crash_replay_tests
    oms_crash_replay_tests.cpp)
target_link_libraries(hepta_oms_crash_replay_tests
    pthread hepta_observability_core
    hepta_oms_core)
hepta_register_reliability_test(hepta_oms_crash_replay_tests)

add_executable(hepta_protocol_fuzz_smoke_tests
    protocol_fuzz_smoke_tests.cpp)
target_link_libraries(hepta_protocol_fuzz_smoke_tests
    hepta_execution_contract)
hepta_register_reliability_test(hepta_protocol_fuzz_smoke_tests)

add_executable(hepta_risk_latency_fixture_tests
    risk_latency_fixture_tests.cpp)
target_link_libraries(hepta_risk_latency_fixture_tests
    hepta_risk_core)
target_compile_definitions(hepta_risk_latency_fixture_tests PRIVATE
    HEPTA_RISK_P99_BASELINE_US=50000)
hepta_register_reliability_test(hepta_risk_latency_fixture_tests)
set_tests_properties(hepta_risk_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance")

hepta_load_latency_baseline(
    HEPTA_GATEWAY
    "benchmarks/gateway-control-latency-baseline-v1.json"
    "gateway-validated-read-dispatch-v1")
add_executable(hepta_gateway_latency_fixture_tests
    gateway_latency_fixture_tests.cpp)
target_link_libraries(hepta_gateway_latency_fixture_tests
    hepta_trading_tool_core)
target_compile_definitions(hepta_gateway_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_GATEWAY_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_GATEWAY_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_gateway_latency_fixture_tests)
set_tests_properties(hepta_gateway_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" TIMEOUT 60)

hepta_load_latency_baseline(
    HEPTA_SNAPSHOT
    "benchmarks/decision-snapshot-latency-baseline-v1.json"
    "decision-snapshot-capture-v1")
add_executable(hepta_snapshot_latency_fixture_tests
    snapshot_latency_fixture_tests.cpp)
target_link_libraries(hepta_snapshot_latency_fixture_tests
    hepta_trading_tool_core)
target_compile_definitions(hepta_snapshot_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_SNAPSHOT_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_SNAPSHOT_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_snapshot_latency_fixture_tests)
set_tests_properties(hepta_snapshot_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" TIMEOUT 90)

hepta_load_latency_baseline(
    HEPTA_PORTFOLIO
    "benchmarks/portfolio-compiler-latency-baseline-v1.json"
    "portfolio-compiler-limit-v1")
add_executable(hepta_portfolio_latency_fixture_tests
    portfolio_latency_fixture_tests.cpp)
target_link_libraries(hepta_portfolio_latency_fixture_tests
    hepta_portfolio_core)
target_compile_definitions(hepta_portfolio_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_PORTFOLIO_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_PORTFOLIO_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_portfolio_latency_fixture_tests)
set_tests_properties(hepta_portfolio_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" TIMEOUT 90)

hepta_load_latency_baseline(
    HEPTA_OMS_JOURNAL_CI
    "benchmarks/oms-journal-ci-latency-baseline-v1.json"
    "oms-critical-durable-append-ci-v1")
add_executable(hepta_oms_journal_latency_fixture_tests
    oms_journal_latency_fixture_tests.cpp)
target_link_libraries(hepta_oms_journal_latency_fixture_tests
    pthread hepta_observability_core hepta_oms_core)
target_compile_definitions(hepta_oms_journal_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_OMS_JOURNAL_CI_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_OMS_JOURNAL_CI_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_oms_journal_latency_fixture_tests)
set_tests_properties(hepta_oms_journal_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" RUN_SERIAL TRUE TIMEOUT 120)

add_custom_target(hepta_reliability_test_binaries
    DEPENDS ${HEPTA_RELIABILITY_TEST_TARGETS})

add_custom_target(hepta_core_test_binaries
    DEPENDS ${HEPTA_CORE_TEST_TARGETS})
