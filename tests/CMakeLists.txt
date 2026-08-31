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

add_executable(hepta_trading_contract_tests
    trading_contract_tests.cpp)
hepta_register_core_test(hepta_trading_contract_tests)

add_executable(hepta_native_tool_client_tests
    native_tool_client_tests.cpp)
target_link_libraries(hepta_native_tool_client_tests
    hepta_native_tool_client)
hepta_register_core_test(hepta_native_tool_client_tests)

add_executable(hepta_execution_coordinator_tests
    execution_coordinator_tests.cpp
    ../HeptaTrade/oms_recover.cpp)
target_link_libraries(hepta_execution_coordinator_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_coordinator_tests)

add_executable(hepta_oms_journal_durability_tests
    oms_journal_durability_tests.cpp
    ../HeptaTrade/oms_journal.cpp)
target_link_libraries(hepta_oms_journal_durability_tests
    pthread hepta_observability_core)
hepta_register_core_test(hepta_oms_journal_durability_tests)

add_executable(hepta_trading_tool_registry_tests
    trading_tool_registry_tests.cpp)
target_link_libraries(hepta_trading_tool_registry_tests
    hepta_trading_tool_core hepta_execution_core)
hepta_register_core_test(hepta_trading_tool_registry_tests)

add_executable(hepta_execution_event_hub_tests
    execution_event_hub_tests.cpp
    ../HeptaTrade/events/owner_scoped_health_publisher.cpp)
target_link_libraries(hepta_execution_event_hub_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_event_hub_tests)

add_executable(hepta_execution_event_feed_tests
    execution_event_feed_tests.cpp
    ../HeptaTrade/tool_host/execution_event_relay.cpp)
target_link_libraries(hepta_execution_event_feed_tests
    hepta_execution_core)
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
    unix_tool_server_test_interposer.cpp
    ../HeptaTrade/tool_host/unix_tool_client.cpp)
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
    agent_simulator_e2e_tests.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
    ../HeptaTrade/simulator/deterministic_execution_venue.cpp)
target_link_libraries(hepta_agent_simulator_e2e_tests
    hepta_agent_os_core hepta_execution_core)
hepta_register_core_test(hepta_agent_simulator_e2e_tests)
set_tests_properties(hepta_agent_simulator_e2e_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_decision_lease_manager_tests
    decision_lease_manager_tests.cpp
    ../HeptaTrade/agent/decision_lease_manager.cpp)
target_link_libraries(hepta_decision_lease_manager_tests pthread)
hepta_register_core_test(hepta_decision_lease_manager_tests)

add_executable(hepta_execution_decision_lease_authority_tests
    execution_decision_lease_authority_tests.cpp)
target_link_libraries(hepta_execution_decision_lease_authority_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_decision_lease_authority_tests)

add_executable(hepta_authoritative_trading_snapshot_store_tests
    authoritative_trading_snapshot_store_tests.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp)
target_link_libraries(hepta_authoritative_trading_snapshot_store_tests pthread)
hepta_register_core_test(hepta_authoritative_trading_snapshot_store_tests)

add_executable(hepta_snapshot_refresh_coordinator_tests
    snapshot_refresh_coordinator_tests.cpp
    ../HeptaTrade/state/snapshot_refresh_coordinator.cpp)
target_link_libraries(hepta_snapshot_refresh_coordinator_tests pthread)
hepta_register_core_test(hepta_snapshot_refresh_coordinator_tests)

add_executable(hepta_ib_order_lifecycle_tests
    ib_order_lifecycle_tests.cpp
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp)
hepta_register_core_test(hepta_ib_order_lifecycle_tests)

add_executable(hepta_ib_gateway_adapter_risk_tests
    ib_gateway_adapter_risk_tests.cpp
    ../HeptaTrade/adapter_ib/ib_api_wrapper.cpp
    ../HeptaTrade/adapter_ib/ib_decimal_compat.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_terminal.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp
    ../HeptaTrade/adapter_ib/ib_venue_correlation.cpp)
target_link_libraries(hepta_ib_gateway_adapter_risk_tests
    hepta_risk_core hepta_observability_core pthread)
hepta_register_core_test(hepta_ib_gateway_adapter_risk_tests)

add_executable(hepta_ib_paper_kill_switch_tests
    ib_paper_kill_switch_tests.cpp
    ../HeptaTrade/execution/ib_paper_kill_switch.cpp)
target_link_libraries(hepta_ib_paper_kill_switch_tests pthread)
hepta_register_core_test(hepta_ib_paper_kill_switch_tests)

add_executable(hepta_deterministic_risk_policy_tests
    deterministic_risk_policy_tests.cpp)
target_link_libraries(hepta_deterministic_risk_policy_tests
    hepta_risk_core)
hepta_register_core_test(hepta_deterministic_risk_policy_tests)

add_executable(hepta_target_position_intent_tests
    target_position_intent_tests.cpp
    ../HeptaTrade/intent/target_position_intent.cpp)
target_link_libraries(hepta_target_position_intent_tests OpenSSL::Crypto)
hepta_register_core_test(hepta_target_position_intent_tests)

add_executable(hepta_authoritative_decision_snapshot_tests
    authoritative_decision_snapshot_tests.cpp
    ../HeptaTrade/intent/authoritative_decision_snapshot.cpp
    ../HeptaTrade/intent/bounded_json.cpp
    ../HeptaTrade/intent/target_position_intent.cpp)
target_link_libraries(hepta_authoritative_decision_snapshot_tests OpenSSL::Crypto)
hepta_register_core_test(hepta_authoritative_decision_snapshot_tests)

add_executable(hepta_unsupported_venue_adapter_tests
    unsupported_venue_adapter_tests.cpp
    ../HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp
    ../HeptaTrade/adapter_xt/xt_gateway_adapter.cpp)
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

add_executable(hepta_oms_crash_replay_tests
    oms_crash_replay_tests.cpp
    ../HeptaTrade/oms_journal.cpp)
target_link_libraries(hepta_oms_crash_replay_tests
    pthread hepta_observability_core)
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

add_custom_target(hepta_reliability_test_binaries
    DEPENDS ${HEPTA_RELIABILITY_TEST_TARGETS})

add_custom_target(hepta_core_test_binaries
    DEPENDS ${HEPTA_CORE_TEST_TARGETS})
