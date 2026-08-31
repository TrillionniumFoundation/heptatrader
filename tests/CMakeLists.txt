find_package(OpenSSL REQUIRED)

set(HEPTA_CORE_TEST_TARGETS)

function(hepta_register_core_test target)
    target_include_directories(${target} PRIVATE
        ../HeptaTrade
        ../HeptaTrade/adapter_ib)
    set_target_properties(${target} PROPERTIES
        CXX_STANDARD 11
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    # The retained test programs use assert() for both verification and
    # fixture setup.  Letting Release define NDEBUG would erase required side
    # effects and turn the test itself into undefined behaviour.  Production
    # targets retain their normal NDEBUG policy; only test translation units
    # explicitly undefine it in every configuration.
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

add_executable(hepta_pre_trade_risk_engine_tests
    pre_trade_risk_engine_tests.cpp
    ../HeptaTrade/risk/pre_trade_risk_engine.cpp)
hepta_register_core_test(hepta_pre_trade_risk_engine_tests)
set_tests_properties(hepta_pre_trade_risk_engine_tests PROPERTIES
    LABELS "core;risk")

add_executable(hepta_unsupported_venue_adapter_tests
    unsupported_venue_adapter_tests.cpp
    ../HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp
    ../HeptaTrade/adapter_xt/xt_gateway_adapter.cpp)
hepta_register_core_test(hepta_unsupported_venue_adapter_tests)
set_tests_properties(hepta_unsupported_venue_adapter_tests PROPERTIES
    LABELS "core;venue-safety")

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
target_link_libraries(hepta_oms_journal_durability_tests pthread)
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
    ../HeptaTrade/tool_host/unix_tool_client.cpp)
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
# The retained legacy test translation unit uses std::sort but historically
# obtained <algorithm> through a transitive include. Make the dependency
# explicit at the target boundary without editing the 160 KiB fixture file.
target_precompile_headers(hepta_unix_session_supervisor_server_tests PRIVATE
    <algorithm>)
hepta_register_core_test(hepta_unix_session_supervisor_server_tests)
set_tests_properties(hepta_unix_session_supervisor_server_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

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

add_executable(hepta_ib_paper_kill_switch_tests
    ib_paper_kill_switch_tests.cpp
    ../HeptaTrade/execution/ib_paper_kill_switch.cpp)
target_link_libraries(hepta_ib_paper_kill_switch_tests pthread)
hepta_register_core_test(hepta_ib_paper_kill_switch_tests)

add_custom_target(hepta_core_test_binaries
    DEPENDS ${HEPTA_CORE_TEST_TARGETS})
