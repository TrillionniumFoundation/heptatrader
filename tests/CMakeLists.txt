set(HEPTA_EXECUTION_COORDINATOR_SOURCES
    ../HeptaTrade/execution/execution_coordinator.cpp
    ../HeptaTrade/execution/execution_coordinator_cancel.cpp
    ../HeptaTrade/execution/execution_coordinator_recovery.cpp
    ../HeptaTrade/execution/execution_coordinator_reconnect.cpp
    ../HeptaTrade/execution/execution_coordinator_terminal.cpp
    ../HeptaTrade/execution/paper_terminal_mutation_manifest.cpp
    ../HeptaTrade/execution/execution_place_order_dispatch.cpp
    ../HeptaTrade/execution/execution_authoritative_flatten.cpp
    ../HeptaTrade/execution/execution_authoritative_flatten_dispatch.cpp
    ../HeptaTrade/oms_journal.cpp
)

set(HEPTA_EXECUTION_SERVICE_RUNTIME_SOURCES
    ../HeptaTrade/execution/execution_service_runtime_config.cpp
    ../HeptaTrade/execution/execution_service_runtime_composition.cpp
)

set(HEPTA_IB_PAPER_POLICY_SOURCES
    ../HeptaTrade/execution/ib_paper_kill_switch.cpp
    ../HeptaTrade/execution/ib_paper_execution_profile.cpp
    ../HeptaTrade/execution/ib_paper_authoritative_flatten.cpp
    ../HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp
    ../HeptaTrade/execution/ib_paper_flatten_plan_binding.cpp
)

set(HEPTA_IB_PAPER_RUNTIME_SOURCES
    ../HeptaTrade/execution/ib_paper_execution_runtime_config.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_composition.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_state.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_terminal_state.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_events.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_event_drain.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_quote_admission.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_recent_orders.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_broker.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_startup.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_policy.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_terminal.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_flatten.cpp
    ../HeptaTrade/execution/ib_paper_execution_hook_authority.cpp
)

set(HEPTA_IB_ADAPTER_TRANSPORT_SOURCES
    ../HeptaTrade/adapter_ib/ib_api_wrapper.cpp
    ../HeptaTrade/adapter_ib/ib_decimal_compat.cpp
)

set(HEPTA_IB_GATEWAY_ADAPTER_SOURCES
    ../HeptaTrade/adapter_ib/ib_gateway_adapter.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_terminal.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp
    ../HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp
)

set(HEPTA_PAPER_TERMINAL_EXTERNAL_LATCH_SOURCES
    ../HeptaTrade/execution/paper_terminal_external_latch.cpp
)

add_executable(hepta_trading_contract_tests
    trading_contract_tests.cpp
)
target_include_directories(hepta_trading_contract_tests PRIVATE ../HeptaTrade)
add_test(NAME hepta_trading_contract_tests COMMAND hepta_trading_contract_tests)

add_executable(hepta_native_tool_client_tests
    native_tool_client_tests.cpp
)
target_include_directories(hepta_native_tool_client_tests PRIVATE ../HeptaTrade)
target_link_libraries(hepta_native_tool_client_tests hepta_native_tool_client)
add_test(NAME hepta_native_tool_client_tests COMMAND hepta_native_tool_client_tests)

add_executable(hepta_execution_coordinator_tests
    execution_coordinator_tests.cpp
    ../HeptaTrade/oms_recover.cpp
)

target_include_directories(hepta_execution_coordinator_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)

target_link_libraries(hepta_execution_coordinator_tests hepta_execution_core)

add_test(NAME hepta_execution_coordinator_tests COMMAND hepta_execution_coordinator_tests)

add_executable(hepta_oms_journal_durability_tests
    oms_journal_durability_tests.cpp
    ../HeptaTrade/oms_journal.cpp
)
target_include_directories(hepta_oms_journal_durability_tests PRIVATE
    ../HeptaTrade)
target_link_libraries(hepta_oms_journal_durability_tests pthread)
set_target_properties(hepta_oms_journal_durability_tests PROPERTIES
    CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
if(MSVC)
    target_compile_options(hepta_oms_journal_durability_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_oms_journal_durability_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME hepta_oms_journal_durability_tests
         COMMAND hepta_oms_journal_durability_tests)

add_executable(hepta_trading_tool_registry_tests
    trading_tool_registry_tests.cpp
)

target_include_directories(hepta_trading_tool_registry_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)

target_link_libraries(hepta_trading_tool_registry_tests
    hepta_trading_tool_core hepta_execution_core)

add_test(NAME hepta_trading_tool_registry_tests COMMAND hepta_trading_tool_registry_tests)

add_executable(hepta_execution_event_hub_tests
    execution_event_hub_tests.cpp
    ../HeptaTrade/events/owner_scoped_health_publisher.cpp
)

target_include_directories(hepta_execution_event_hub_tests PRIVATE
    ../HeptaTrade
)

target_link_libraries(hepta_execution_event_hub_tests hepta_execution_core)

add_test(NAME hepta_execution_event_hub_tests COMMAND hepta_execution_event_hub_tests)

add_executable(hepta_execution_event_feed_tests
    execution_event_feed_tests.cpp
    ../HeptaTrade/tool_host/execution_event_relay.cpp
)
target_include_directories(hepta_execution_event_feed_tests PRIVATE
    ../HeptaTrade
)
target_link_libraries(hepta_execution_event_feed_tests hepta_execution_core)
add_test(NAME hepta_execution_event_feed_tests COMMAND hepta_execution_event_feed_tests)

add_executable(hepta_execution_gateway_runtime_composition_tests
    execution_gateway_runtime_composition_tests.cpp
)
target_include_directories(hepta_execution_gateway_runtime_composition_tests PRIVATE
    ../HeptaTrade
)
target_link_libraries(hepta_execution_gateway_runtime_composition_tests hepta_agent_os_core)
target_link_libraries(hepta_execution_gateway_runtime_composition_tests
    hepta_execution_server)
add_test(NAME hepta_execution_gateway_runtime_composition_tests
         COMMAND hepta_execution_gateway_runtime_composition_tests)

add_executable(hepta_trading_tool_host_tests
    trading_tool_host_tests.cpp
)

target_include_directories(hepta_trading_tool_host_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)

target_link_libraries(hepta_trading_tool_host_tests
    hepta_agent_os_core hepta_execution_core)

add_test(NAME hepta_trading_tool_host_tests COMMAND hepta_trading_tool_host_tests)

add_executable(hepta_unix_tool_server_tests
    unix_tool_server_tests.cpp
    ../HeptaTrade/tool_host/unix_tool_client.cpp
)

target_include_directories(hepta_unix_tool_server_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)

target_link_libraries(hepta_unix_tool_server_tests
    hepta_agent_os_core hepta_execution_core hepta_native_tool_client)

add_test(NAME hepta_unix_tool_server_tests COMMAND hepta_unix_tool_server_tests)
set_tests_properties(hepta_unix_tool_server_tests PROPERTIES
    RUN_SERIAL TRUE
    TIMEOUT 120)

add_executable(hepta_unix_session_supervisor_server_tests
    unix_session_supervisor_server_tests.cpp
)
target_include_directories(hepta_unix_session_supervisor_server_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
find_package(OpenSSL REQUIRED)
target_link_libraries(hepta_unix_session_supervisor_server_tests
    hepta_agent_os_core hepta_execution_core)
add_dependencies(hepta_unix_session_supervisor_server_tests hepta_sessionctl)
target_compile_definitions(hepta_unix_session_supervisor_server_tests PRIVATE
    HEPTA_SESSIONCTL_TEST_BINARY="$<TARGET_FILE:hepta_sessionctl>")
add_test(NAME hepta_unix_session_supervisor_server_tests
         COMMAND hepta_unix_session_supervisor_server_tests)

add_executable(hepta_session_supervisor_lease_store_migration_tests
    session_supervisor_lease_store_migration_tests.cpp
)
target_include_directories(
    hepta_session_supervisor_lease_store_migration_tests PRIVATE
    ../HeptaTrade
)
target_link_libraries(hepta_session_supervisor_lease_store_migration_tests
    hepta_agent_os_core
)
add_test(NAME hepta_session_supervisor_lease_store_migration_tests
         COMMAND hepta_session_supervisor_lease_store_migration_tests)

add_executable(hepta_agent_simulator_e2e_tests
    agent_simulator_e2e_tests.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
    ../HeptaTrade/simulator/deterministic_execution_venue.cpp
)

target_include_directories(hepta_agent_simulator_e2e_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_agent_simulator_e2e_tests
    hepta_agent_os_core hepta_execution_core)
add_test(NAME hepta_agent_simulator_e2e_tests COMMAND hepta_agent_simulator_e2e_tests)
set_tests_properties(hepta_agent_simulator_e2e_tests PROPERTIES
    RUN_SERIAL TRUE
    TIMEOUT 120)

add_executable(hepta_decision_lease_manager_tests
    decision_lease_manager_tests.cpp
    ../HeptaTrade/agent/decision_lease_manager.cpp
)
target_include_directories(hepta_decision_lease_manager_tests PRIVATE ../HeptaTrade)
target_link_libraries(hepta_decision_lease_manager_tests pthread)
add_test(NAME hepta_decision_lease_manager_tests COMMAND hepta_decision_lease_manager_tests)

add_executable(hepta_execution_decision_lease_authority_tests
    execution_decision_lease_authority_tests.cpp
)
target_include_directories(hepta_execution_decision_lease_authority_tests PRIVATE
    ../HeptaTrade)
target_link_libraries(hepta_execution_decision_lease_authority_tests hepta_execution_core)
add_test(NAME hepta_execution_decision_lease_authority_tests
         COMMAND hepta_execution_decision_lease_authority_tests)

add_executable(hepta_authoritative_trading_snapshot_store_tests
    authoritative_trading_snapshot_store_tests.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
)
target_include_directories(hepta_authoritative_trading_snapshot_store_tests PRIVATE ../HeptaTrade)
target_link_libraries(hepta_authoritative_trading_snapshot_store_tests pthread)
add_test(NAME hepta_authoritative_trading_snapshot_store_tests
         COMMAND hepta_authoritative_trading_snapshot_store_tests)

add_executable(hepta_snapshot_refresh_coordinator_tests
    snapshot_refresh_coordinator_tests.cpp
    ../HeptaTrade/state/snapshot_refresh_coordinator.cpp
)
target_include_directories(hepta_snapshot_refresh_coordinator_tests PRIVATE ../HeptaTrade)
target_link_libraries(hepta_snapshot_refresh_coordinator_tests pthread)
add_test(NAME hepta_snapshot_refresh_coordinator_tests
         COMMAND hepta_snapshot_refresh_coordinator_tests)

add_executable(hepta_ib_authoritative_event_queue_tests
    ib_authoritative_event_queue_tests.cpp
    ../HeptaTrade/adapter_ib/ib_api_wrapper.cpp
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp
    ../HeptaTrade/adapter_ib/ib_venue_correlation.cpp
    ../HeptaTrade/risk/pre_trade_risk_engine.cpp
)
target_include_directories(hepta_ib_authoritative_event_queue_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
set_target_properties(hepta_ib_authoritative_event_queue_tests PROPERTIES
    CXX_STANDARD 11
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
)
if(MSVC)
    target_compile_options(hepta_ib_authoritative_event_queue_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_authoritative_event_queue_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
target_link_libraries(hepta_ib_authoritative_event_queue_tests pthread)
add_test(NAME hepta_ib_authoritative_event_queue_tests
         COMMAND hepta_ib_authoritative_event_queue_tests)

add_executable(hepta_ib_order_lifecycle_tests
    ib_order_lifecycle_tests.cpp
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp
)
target_include_directories(hepta_ib_order_lifecycle_tests PRIVATE
    ../HeptaTrade)
set_target_properties(hepta_ib_order_lifecycle_tests PROPERTIES
    CXX_STANDARD 11
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
)
if(MSVC)
    target_compile_options(hepta_ib_order_lifecycle_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_order_lifecycle_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME hepta_ib_order_lifecycle_tests
         COMMAND hepta_ib_order_lifecycle_tests)

add_executable(hepta_ib_paper_execution_profile_tests
    ib_paper_execution_profile_tests.cpp
    ${HEPTA_IB_PAPER_POLICY_SOURCES}
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
)
target_include_directories(hepta_ib_paper_execution_profile_tests PRIVATE
    ../HeptaTrade
)
set_target_properties(hepta_ib_paper_execution_profile_tests PROPERTIES
    CXX_STANDARD 11
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
)
if(MSVC)
    target_compile_options(hepta_ib_paper_execution_profile_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_paper_execution_profile_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
target_link_libraries(hepta_ib_paper_execution_profile_tests
    pthread OpenSSL::Crypto)
add_test(NAME hepta_ib_paper_execution_profile_tests
         COMMAND hepta_ib_paper_execution_profile_tests)

add_executable(hepta_ib_paper_kill_switch_tests
    ib_paper_kill_switch_tests.cpp
    ../HeptaTrade/execution/ib_paper_kill_switch.cpp
)
target_include_directories(hepta_ib_paper_kill_switch_tests PRIVATE
    ../HeptaTrade
)
set_target_properties(hepta_ib_paper_kill_switch_tests PROPERTIES
    CXX_STANDARD 11
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
)
if(MSVC)
    target_compile_options(hepta_ib_paper_kill_switch_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_paper_kill_switch_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
target_link_libraries(hepta_ib_paper_kill_switch_tests pthread)
add_test(NAME hepta_ib_paper_kill_switch_tests
         COMMAND hepta_ib_paper_kill_switch_tests)

add_executable(hepta_ib_paper_execution_runtime_config_tests
    ib_paper_execution_runtime_config_tests.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_config.cpp
    ${HEPTA_IB_PAPER_POLICY_SOURCES}
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
)
target_include_directories(hepta_ib_paper_execution_runtime_config_tests PRIVATE
    ../HeptaTrade
)
set_target_properties(hepta_ib_paper_execution_runtime_config_tests PROPERTIES
    CXX_STANDARD 11
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
)
if(MSVC)
    target_compile_options(hepta_ib_paper_execution_runtime_config_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_paper_execution_runtime_config_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
target_link_libraries(hepta_ib_paper_execution_runtime_config_tests
    pthread OpenSSL::Crypto)
add_test(NAME hepta_ib_paper_execution_runtime_config_tests
         COMMAND hepta_ib_paper_execution_runtime_config_tests)

add_executable(hepta_ib_paper_execution_runtime_composition_tests
    ib_paper_execution_runtime_composition_tests.cpp
    ../HeptaTrade/agent/decision_lease_manager.cpp
    ../HeptaTrade/execution/execution_decision_lease_authority.cpp
    ${HEPTA_IB_PAPER_RUNTIME_SOURCES}
    ${HEPTA_IB_PAPER_POLICY_SOURCES}
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
    ../HeptaTrade/execution/execution_event_feed.cpp
    ../HeptaTrade/execution/execution_event_feed_client.cpp
    ../HeptaTrade/execution/execution_event_feed_server.cpp
    ../HeptaTrade/execution/execution_event_feed_transport.cpp
    ../HeptaTrade/execution/execution_service_protocol.cpp
    ../HeptaTrade/execution/unix_execution_service.cpp
    ../HeptaTrade/execution/unix_execution_service_flatten.cpp
    ../HeptaTrade/execution/unix_execution_service_flatten_permit.cpp
    ../HeptaTrade/execution/unix_execution_service_client.cpp
    ../HeptaTrade/execution/unix_execution_service_flatten_client.cpp
    ../HeptaTrade/execution/unix_execution_service_transport.cpp
    ../HeptaTrade/events/execution_event_hub.cpp
    ${HEPTA_IB_ADAPTER_TRANSPORT_SOURCES}
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp
    ../HeptaTrade/adapter_ib/ib_venue_correlation.cpp
    ../HeptaTrade/risk/pre_trade_risk_engine.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
    ../HeptaTrade/state/ib_authoritative_quote_subscription_set.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
)
target_include_directories(hepta_ib_paper_execution_runtime_composition_tests PRIVATE
    ../HeptaTrade ../HeptaTrade/adapter_ib)
target_link_libraries(hepta_ib_paper_execution_runtime_composition_tests
    pthread OpenSSL::Crypto)
set_target_properties(hepta_ib_paper_execution_runtime_composition_tests PROPERTIES
    CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
if(MSVC)
    target_compile_options(hepta_ib_paper_execution_runtime_composition_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_paper_execution_runtime_composition_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME hepta_ib_paper_execution_runtime_composition_tests
         COMMAND hepta_ib_paper_execution_runtime_composition_tests)
set_tests_properties(
    hepta_ib_paper_execution_runtime_composition_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_ib_paper_execution_process_e2e_tests
    ib_paper_execution_process_e2e_tests.cpp
    ../HeptaTrade/agent/decision_lease_manager.cpp
    ../HeptaTrade/execution/execution_decision_lease_authority.cpp
    ${HEPTA_IB_PAPER_RUNTIME_SOURCES}
    ${HEPTA_IB_PAPER_POLICY_SOURCES}
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
    ../HeptaTrade/execution/execution_event_feed.cpp
    ../HeptaTrade/execution/execution_event_feed_client.cpp
    ../HeptaTrade/execution/execution_event_feed_server.cpp
    ../HeptaTrade/execution/execution_event_feed_transport.cpp
    ../HeptaTrade/execution/execution_service_protocol.cpp
    ../HeptaTrade/execution/unix_execution_service.cpp
    ../HeptaTrade/execution/unix_execution_service_flatten.cpp
    ../HeptaTrade/execution/unix_execution_service_flatten_permit.cpp
    ../HeptaTrade/execution/unix_execution_service_client.cpp
    ../HeptaTrade/execution/unix_execution_service_flatten_client.cpp
    ../HeptaTrade/execution/unix_execution_service_transport.cpp
    ../HeptaTrade/events/execution_event_hub.cpp
    ${HEPTA_IB_ADAPTER_TRANSPORT_SOURCES}
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp
    ../HeptaTrade/adapter_ib/ib_venue_correlation.cpp
    ../HeptaTrade/risk/pre_trade_risk_engine.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
    ../HeptaTrade/state/ib_authoritative_quote_subscription_set.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
)
target_include_directories(hepta_ib_paper_execution_process_e2e_tests PRIVATE
    ../HeptaTrade ../HeptaTrade/adapter_ib)
target_link_libraries(hepta_ib_paper_execution_process_e2e_tests
    pthread OpenSSL::Crypto)
set_target_properties(hepta_ib_paper_execution_process_e2e_tests PROPERTIES
    CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
if(MSVC)
    target_compile_options(hepta_ib_paper_execution_process_e2e_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_paper_execution_process_e2e_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME hepta_ib_paper_execution_process_e2e_tests
         COMMAND hepta_ib_paper_execution_process_e2e_tests)
set_tests_properties(hepta_ib_paper_execution_process_e2e_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_ib_authoritative_order_projector_tests
    ib_authoritative_order_projector_tests.cpp
    ../HeptaTrade/state/ib_authoritative_order_projector.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
)
target_include_directories(hepta_ib_authoritative_order_projector_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_ib_authoritative_order_projector_tests pthread OpenSSL::Crypto)
add_test(NAME hepta_ib_authoritative_order_projector_tests
         COMMAND hepta_ib_authoritative_order_projector_tests)

add_executable(hepta_ib_authoritative_account_position_consumer_tests
    ib_authoritative_account_position_consumer_tests.cpp
    ../HeptaTrade/state/ib_authoritative_account_position_consumer.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
)
target_include_directories(hepta_ib_authoritative_account_position_consumer_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_ib_authoritative_account_position_consumer_tests pthread)
add_test(NAME hepta_ib_authoritative_account_position_consumer_tests
         COMMAND hepta_ib_authoritative_account_position_consumer_tests)

add_executable(hepta_ib_authoritative_open_order_consumer_tests
    ib_authoritative_open_order_consumer_tests.cpp
    ../HeptaTrade/state/ib_authoritative_open_order_consumer.cpp
    ../HeptaTrade/state/ib_authoritative_order_projector.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
)
target_include_directories(hepta_ib_authoritative_open_order_consumer_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_ib_authoritative_open_order_consumer_tests pthread)
add_test(NAME hepta_ib_authoritative_open_order_consumer_tests
         COMMAND hepta_ib_authoritative_open_order_consumer_tests)

add_executable(hepta_ib_authoritative_quote_subscription_set_tests
    ib_authoritative_quote_subscription_set_tests.cpp
    ../HeptaTrade/state/ib_authoritative_quote_subscription_set.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
)
target_include_directories(hepta_ib_authoritative_quote_subscription_set_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_ib_authoritative_quote_subscription_set_tests pthread)
add_test(NAME hepta_ib_authoritative_quote_subscription_set_tests
         COMMAND hepta_ib_authoritative_quote_subscription_set_tests)

add_executable(hepta_ib_authoritative_recovery_coordinator_tests
    ib_authoritative_recovery_coordinator_tests.cpp
    ../HeptaTrade/state/ib_authoritative_recovery_coordinator.cpp
    ../HeptaTrade/state/snapshot_refresh_coordinator.cpp
)
target_include_directories(hepta_ib_authoritative_recovery_coordinator_tests PRIVATE
    ../HeptaTrade
)
target_link_libraries(hepta_ib_authoritative_recovery_coordinator_tests pthread)
add_test(NAME hepta_ib_authoritative_recovery_coordinator_tests
         COMMAND hepta_ib_authoritative_recovery_coordinator_tests)

add_executable(hepta_trading_tool_session_catalog_tests
    trading_tool_session_catalog_tests.cpp
    ../HeptaTrade/tool_host/trading_tool_session_catalog.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
)
target_include_directories(hepta_trading_tool_session_catalog_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_trading_tool_session_catalog_tests pthread)
add_test(NAME hepta_trading_tool_session_catalog_tests
         COMMAND hepta_trading_tool_session_catalog_tests)

add_executable(hepta_ib_authoritative_recovery_event_consumer_tests
    ib_authoritative_recovery_event_consumer_tests.cpp
    ../HeptaTrade/state/ib_authoritative_recovery_event_consumer.cpp
    ../HeptaTrade/state/ib_authoritative_quote_subscription_set.cpp
    ../HeptaTrade/state/ib_authoritative_recovery_coordinator.cpp
    ../HeptaTrade/state/snapshot_refresh_coordinator.cpp
    ../HeptaTrade/state/ib_authoritative_account_position_consumer.cpp
    ../HeptaTrade/state/ib_authoritative_open_order_consumer.cpp
    ../HeptaTrade/state/ib_authoritative_order_projector.cpp
    ../HeptaTrade/state/ib_contract_identity.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
)
target_include_directories(hepta_ib_authoritative_recovery_event_consumer_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_ib_authoritative_recovery_event_consumer_tests pthread)
add_test(NAME hepta_ib_authoritative_recovery_event_consumer_tests
         COMMAND hepta_ib_authoritative_recovery_event_consumer_tests)

add_executable(hepta_ib_connection_lifecycle_state_machine_tests
    ib_connection_lifecycle_state_machine_tests.cpp
    ../HeptaTrade/state/ib_connection_lifecycle_state_machine.cpp
)
target_include_directories(hepta_ib_connection_lifecycle_state_machine_tests PRIVATE
    ../HeptaTrade
)
target_link_libraries(hepta_ib_connection_lifecycle_state_machine_tests pthread)
add_test(NAME hepta_ib_connection_lifecycle_state_machine_tests
         COMMAND hepta_ib_connection_lifecycle_state_machine_tests)

find_program(HEPTA_PYTHON3_EXECUTABLE python3 REQUIRED)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_test(NAME hepta_local_paper_control_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_local_paper_control_tests.py)
    set_tests_properties(hepta_local_paper_control_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_local_paper_recovery_guard_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_local_paper_recovery_guard_tests.py)
    set_tests_properties(hepta_local_paper_recovery_guard_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_local_paper_repair_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_local_paper_repair_tests.py)
    set_tests_properties(hepta_local_paper_repair_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_local_paper_supervisor_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_local_paper_supervisor_tests.py)
    set_tests_properties(hepta_local_paper_supervisor_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    set(HEPTA_EXECUTION_INSTALL_TREE_TEST_ARGS
        --build-dir ${CMAKE_BINARY_DIR}
        --cmake ${CMAKE_COMMAND})
    if(HEPTA_ENABLE_IBAPI)
        list(APPEND HEPTA_EXECUTION_INSTALL_TREE_TEST_ARGS --ibapi-enabled)
    endif()
    add_test(NAME hepta_execution_install_tree_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/check_hepta_execution_install_tree.py
                     ${HEPTA_EXECUTION_INSTALL_TREE_TEST_ARGS})
    set_tests_properties(hepta_execution_install_tree_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 60)
    add_test(NAME hepta_agent_os_install_tree_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/check_hepta_agent_os_install_tree.py
                     --build-dir ${CMAKE_BINARY_DIR}
                     --cmake ${CMAKE_COMMAND})
    set_tests_properties(hepta_agent_os_install_tree_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 60)
    add_test(NAME hepta_agent_os_sdk_install_tree_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/check_hepta_agent_os_sdk_install_tree.py
                     --build-dir ${CMAKE_BINARY_DIR}
                     --source-dir ${CMAKE_SOURCE_DIR}
                     --cmake ${CMAKE_COMMAND})
    set_tests_properties(hepta_agent_os_sdk_install_tree_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_agent_os_identity_permissions_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_agent_os_identity_permissions.py)
    set_tests_properties(hepta_agent_os_identity_permissions_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_agent_session_bootstrap_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_agent_session_bootstrap_tests.py)
    set_tests_properties(hepta_agent_session_bootstrap_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 60
        ENVIRONMENT
            "PYTHONDONTWRITEBYTECODE=1;HEPTA_SESSIONCTL_TEST_BIN=$<TARGET_FILE:hepta_sessionctl>")
    add_test(NAME hepta_shadow_watch_collector_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_shadow_watch_collector_tests.py)
    set_tests_properties(hepta_shadow_watch_collector_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_shadow_watch_custodian_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_shadow_watch_custodian_tests.py)
    set_tests_properties(hepta_shadow_watch_custodian_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_strategy_pipeline_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_strategy_pipeline_tests.py)
    set_tests_properties(hepta_strategy_pipeline_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_market_evidence_normalizer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_market_evidence_normalizer_tests.py)
    set_tests_properties(hepta_market_evidence_normalizer_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_market_official_source_extractor_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_market_official_source_extractor_tests.py)
    set_tests_properties(hepta_market_official_source_extractor_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_shadow_market_history_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_shadow_market_history_tests.py)
    set_tests_properties(hepta_shadow_market_history_tests PROPERTIES
        TIMEOUT 90
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_strategy_replay_evaluator_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_strategy_replay_evaluator_tests.py)
    set_tests_properties(hepta_strategy_replay_evaluator_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_strategy_shadow_runner_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_strategy_shadow_runner_tests.py)
    set_tests_properties(hepta_strategy_shadow_runner_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_bounded_shadow_observer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_bounded_shadow_observer_tests.py)
    set_tests_properties(hepta_bounded_shadow_observer_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_shadow_host_controller_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_shadow_host_controller_tests.py)
    set_tests_properties(hepta_p1_shadow_host_controller_tests PROPERTIES
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_load_probe_validator_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_load_probe_validator_tests.py)
    set_tests_properties(hepta_p1_load_probe_validator_tests PROPERTIES
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_observer_controller_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_observer_controller_tests.py)
    set_tests_properties(hepta_p1_observer_controller_tests PROPERTIES
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_shadow_admission_launcher_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_shadow_admission_launcher_tests.py)
    set_tests_properties(hepta_p1_shadow_admission_launcher_tests PROPERTIES
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_policy_planner_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_policy_planner_tests.py)
    set_tests_properties(hepta_p1_safety_soak_policy_planner_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_campaign_coordinator_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_campaign_coordinator_tests.py)
    set_tests_properties(
        hepta_p1_safety_soak_campaign_coordinator_tests PROPERTIES
        TIMEOUT 180
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_fault_pin_producer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_fault_pin_producer_tests.py)
    set_tests_properties(
        hepta_p1_safety_soak_fault_pin_producer_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_campaign_freezer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_campaign_freezer_tests.py)
    set_tests_properties(
        hepta_p1_safety_soak_campaign_freezer_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_evidence_recorder_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_evidence_recorder_tests.py)
    set_tests_properties(
        hepta_p1_safety_soak_evidence_recorder_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_independent_observer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_independent_observer_tests.py)
    set_tests_properties(
        hepta_p1_safety_soak_independent_observer_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_root_fault_injector_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_root_fault_injector_tests.py)
    set_tests_properties(
        hepta_p1_safety_soak_root_fault_injector_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_safety_soak_auditor_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_safety_soak_auditor_tests.py)
    set_tests_properties(hepta_p1_safety_soak_auditor_tests PROPERTIES
        TIMEOUT 1200
        RUN_SERIAL TRUE
        LABELS "p1-synthetic;nightly;release"
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_watch_to_paper_handoff_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_watch_to_paper_handoff_tests.py)
    set_tests_properties(hepta_p1_watch_to_paper_handoff_tests PROPERTIES
        TIMEOUT 600
        RUN_SERIAL TRUE
        LABELS "p1-synthetic;nightly;release"
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_paper_zero_exposure_snapshot_producer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_zero_exposure_snapshot_producer_tests.py)
    set_tests_properties(
        hepta_p1_paper_zero_exposure_snapshot_producer_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_paper_zero_exposure_attestor_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_zero_exposure_attestor_tests.py)
    set_tests_properties(
        hepta_p1_paper_zero_exposure_attestor_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_rootful_review_closure_consumer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_rootful_review_closure_consumer_tests.py)
    set_tests_properties(
        hepta_rootful_review_closure_consumer_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_rootful_systemd_environment_provenance_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_rootful_systemd_environment_provenance_tests.py)
    set_tests_properties(
        hepta_rootful_systemd_environment_provenance_tests PROPERTIES
        TIMEOUT 180
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_rootful_systemd_base_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_rootful_systemd_base_tests.py)
    set_tests_properties(hepta_rootful_systemd_base_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_systemd_gate_apparmor_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_systemd_gate_apparmor_tests.py)
    set_tests_properties(hepta_systemd_gate_apparmor_tests PROPERTIES
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_paper_admission_verifier_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_admission_verifier_tests.py)
    set_tests_properties(hepta_p1_paper_admission_verifier_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_paper_kill_switch_bootstrap_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_kill_switch_bootstrap_tests.py)
    set_tests_properties(
        hepta_p1_paper_kill_switch_bootstrap_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_release_validation_installed_layout_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_release_validation_installed_layout_tests.py)
    set_tests_properties(
        hepta_release_validation_installed_layout_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_watch_profile_deployer_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_watch_profile_deployer_tests.py)
    set_tests_properties(hepta_p1_watch_profile_deployer_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 180
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_p1_watch_activation_transaction_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_watch_activation_transaction_tests.py)
    set_tests_properties(hepta_p1_watch_activation_transaction_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 180
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_bounded_shadow_closure_verifier_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/hepta_bounded_shadow_closure_verifier_tests.py)
    set_tests_properties(hepta_bounded_shadow_closure_verifier_tests PROPERTIES
        RUN_SERIAL TRUE
        TIMEOUT 300
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_agent_os_provisioned_host_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/check_hepta_agent_os_provisioned_host_fixture.py)
    set_tests_properties(hepta_agent_os_provisioned_host_fixture_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # This test uses a disposable --root fixture and synthetic ownership
    # metadata. It never inspects or provisions the CTest host itself.
    add_test(NAME hepta_execution_provisioned_host_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/check_hepta_execution_provisioned_host_fixture.py)
    set_tests_properties(hepta_execution_provisioned_host_fixture_tests PROPERTIES
        TIMEOUT 30)

    add_test(NAME hepta_execution_gateway_soak_provenance_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_execution_gateway_soak_provenance_fixture.py)
    set_tests_properties(
        hepta_execution_gateway_soak_provenance_fixture_tests PROPERTIES
        TIMEOUT 30)

    # Pure offline runner-contract coverage. This never invokes Docker or
    # systemd; the actual rootful gate remains explicit and opt-in.
    add_test(NAME hepta_execution_rootful_systemd_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_execution_rootful_systemd_gate_fixture.py)
    set_tests_properties(
        hepta_execution_rootful_systemd_gate_fixture_tests PROPERTIES
        TIMEOUT 30)

    # Pure offline four-UID Agent OS runner-contract coverage. This never
    # invokes Docker or systemd; the actual rootful gate remains opt-in.
    add_test(NAME hepta_agent_os_rootful_systemd_e2e_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_agent_os_rootful_systemd_e2e_gate_fixture.py)
    set_tests_properties(
        hepta_agent_os_rootful_systemd_e2e_gate_fixture_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # Pure offline contract coverage for the broker lifecycle runner.
    # It never invokes Docker, nftables, systemd, or a broker.
    add_test(NAME hepta_broker_network_rootful_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_broker_network_rootful_gate_fixture.py)
    set_tests_properties(
        hepta_broker_network_rootful_gate_fixture_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # Pure offline command-fake coverage for the native broker netns hard
    # isolation gate. It never mutates CTest-host networking or systemd.
    add_test(NAME hepta_broker_network_hard_isolation_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_broker_network_hard_isolation_gate_fixture.py)
    set_tests_properties(
        hepta_broker_network_hard_isolation_gate_fixture_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # Pure offline contract coverage for the templated PAPER effective-systemd
    # runner. It never invokes Docker, nftables, systemd, or a broker.
    add_test(NAME hepta_paper_domain_rootful_systemd_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_paper_domain_rootful_systemd_gate_fixture.py)
    set_tests_properties(
        hepta_paper_domain_rootful_systemd_gate_fixture_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # Pure offline fake-Docker coverage for the P1 WATCH/PAPER dual-domain
    # runner. The registered real-runner CTest is separately opt-in and
    # remains non-certifying.
    add_test(NAME hepta_p1_dual_domain_rootful_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_p1_dual_domain_rootful_gate_fixture.py)
    set_tests_properties(
        hepta_p1_dual_domain_rootful_gate_fixture_tests PROPERTIES
        TIMEOUT 60
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # Pure offline fake-Docker and effective-unit contract coverage for the P1
    # campaign coordinator/worker liveness runner. The real CTest remains an
    # explicitly enabled, non-certifying rehearsal.
    add_test(NAME hepta_p1_campaign_rootful_liveness_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_p1_campaign_rootful_liveness_gate_fixture.py)
    set_tests_properties(
        hepta_p1_campaign_rootful_liveness_gate_fixture_tests PROPERTIES
        TIMEOUT 120
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

    # Pure offline native-VM runner contract coverage. This never provisions
    # a VM or starts systemd units on the CTest host.
    add_test(NAME hepta_execution_native_systemd_gate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/run_hepta_execution_native_systemd_gate_fixture.py)
    set_tests_properties(
        hepta_execution_native_systemd_gate_fixture_tests PROPERTIES
        TIMEOUT 30)

    add_test(NAME hepta_execution_native_systemd_aggregate_fixture_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/aggregate_hepta_execution_native_systemd_gate_fixture.py)
    set_tests_properties(
        hepta_execution_native_systemd_aggregate_fixture_tests PROPERTIES
        TIMEOUT 30)

    if(NOT HEPTA_AGENT_OS_SOURCE_ONLY)
        add_test(NAME hepta_execution_native_vm_bundle_fixture_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/build_hepta_execution_native_vm_bundle_fixture.py)
        set_tests_properties(
            hepta_execution_native_vm_bundle_fixture_tests PROPERTIES
            TIMEOUT 30)

        add_test(NAME hepta_execution_native_vm_bundle_verification_fixture_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/verify_hepta_execution_native_vm_bundle_fixture.py)
        set_tests_properties(
            hepta_execution_native_vm_bundle_verification_fixture_tests PROPERTIES
            TIMEOUT 60)
        add_test(NAME hepta_clean_source_path_policy_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/build_heptatrader_clean_source_bundle_tests.py)
        set_tests_properties(hepta_clean_source_path_policy_tests PROPERTIES
            TIMEOUT 30
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_prebuilt_asset_boundary_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_prebuilt_asset_tests.py)
        set_tests_properties(hepta_prebuilt_asset_boundary_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_repository_governance_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/hepta_repository_governance_tests.py)
        set_tests_properties(hepta_repository_governance_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_workspace_layout_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_workspace_layout_tests.py)
        set_tests_properties(hepta_workspace_layout_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_legacy_wrapper_inventory_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_legacy_wrapper_inventory_tests.py)
        set_tests_properties(hepta_legacy_wrapper_inventory_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_verification_evidence_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_verification_evidence_tests.py)
        set_tests_properties(hepta_verification_evidence_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_engineering_closure_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_engineering_closure_tests.py)
        set_tests_properties(hepta_engineering_closure_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_recovery_materialization_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_recovery_materialization_tests.py)
        set_tests_properties(hepta_recovery_materialization_tests PROPERTIES
            TIMEOUT 240
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_workspace_layout_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/audit_heptatrader_workspace_layout.py
                         --root ${CMAKE_SOURCE_DIR}
                         --strict)
        set_tests_properties(hepta_workspace_layout_gate PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_agent_os_source_bundle_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_agent_os_source_bundle_tests.py)
        set_tests_properties(hepta_agent_os_source_bundle_tests PROPERTIES
            TIMEOUT 30
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_agent_os_product_boundary_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_hepta_agent_os_product_boundary.py
                         --root ${CMAKE_SOURCE_DIR})
        set_tests_properties(hepta_agent_os_product_boundary_gate PROPERTIES
            TIMEOUT 30
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_code_quality_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_code_quality_tests.py)
        set_tests_properties(hepta_code_quality_tests PROPERTIES
            TIMEOUT 30
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_code_quality_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_heptatrader_code_quality.py
                         --root ${CMAKE_SOURCE_DIR}
                         --strict)
        set_tests_properties(hepta_code_quality_gate PROPERTIES
            TIMEOUT 30
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    add_test(NAME hepta_plugin_package_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_plugin_package_tests.py)
    set_tests_properties(hepta_plugin_package_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    add_test(NAME hepta_optional_loader_lifecycle_gate_contract_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_optional_loader_lifecycle_gate_tests.py)
    set_tests_properties(
        hepta_optional_loader_lifecycle_gate_contract_tests PROPERTIES
        TIMEOUT 30
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    if(HEPTA_ENABLE_AGENT_OS_USER_SYSTEMD_LIFECYCLE_GATE)
        add_test(NAME hepta_agent_os_user_systemd_lifecycle_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_agent_os_systemd_lifecycle_gate.py
                         --run --require)
        set_tests_properties(
            hepta_agent_os_user_systemd_lifecycle_gate PROPERTIES
            LABELS "explicit-rehearsal;systemd;offline"
            RUN_SERIAL TRUE
            TIMEOUT 180
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(HEPTA_ENABLE_AGENT_OS_ROOTFUL_SYSTEMD_E2E_GATE)
        add_test(NAME hepta_agent_os_rootful_systemd_e2e_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py
                         --run
                         --build-dir ${CMAKE_BINARY_DIR}
                         --base-image
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_IMAGE}"
                         --buildkit-image
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDKIT_IMAGE}"
                         --reviewed-base-provenance
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE}"
                         --reviewed-base-provenance-sha256
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256}"
                         --reviewed-builder-provenance
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}"
                         --reviewed-builder-provenance-sha256
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256}"
                         --buildx-binary-sha256
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDX_BINARY_SHA256}"
                         --apparmor-provenance
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE}"
                         --apparmor-provenance-sha256
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256}"
                         --docker-apparmor-namespace-provenance
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE}"
                         --docker-apparmor-namespace-provenance-sha256
                             "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256}"
                         --report
                             ${CMAKE_BINARY_DIR}/agent-os-rootful-systemd-e2e-gate.json)
        set_tests_properties(
            hepta_agent_os_rootful_systemd_e2e_gate PROPERTIES
            LABELS "explicit-rehearsal;systemd;offline;four-uid"
            RUN_SERIAL TRUE
            RESOURCE_LOCK hepta_agent_os_rootful_systemd_e2e
            TIMEOUT 2400
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(HEPTA_ENABLE_BROKER_NETWORK_ROOTFUL_GATE)
        add_test(NAME hepta_broker_network_rootful_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_broker_network_rootful_gate.py
                         --run
                         --base-image
                             ${HEPTA_BROKER_NETWORK_ROOTFUL_BASE_IMAGE}
                         --report
                             ${CMAKE_BINARY_DIR}/broker-network-rootful-gate.json)
        set_tests_properties(
            hepta_broker_network_rootful_gate PROPERTIES
            LABELS "explicit-rehearsal;network-only;offline"
            RUN_SERIAL TRUE
            RESOURCE_LOCK hepta_broker_network_rootful
            TIMEOUT 1200
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(HEPTA_ENABLE_PAPER_DOMAIN_ROOTFUL_SYSTEMD_GATE)
        add_test(NAME hepta_paper_domain_rootful_systemd_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_paper_domain_rootful_systemd_gate.py
                         --run
                         --base-image
                             ${HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_IMAGE}
                         --report
                             ${CMAKE_BINARY_DIR}/paper-domain-rootful-systemd-gate.json)
        set_tests_properties(
            hepta_paper_domain_rootful_systemd_gate PROPERTIES
            LABELS "explicit-rehearsal;systemd;paper;offline"
            RUN_SERIAL TRUE
            RESOURCE_LOCK hepta_paper_domain_rootful_systemd
            TIMEOUT 1800
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(HEPTA_ENABLE_P1_DUAL_DOMAIN_ROOTFUL_REHEARSAL)
        add_test(NAME hepta_p1_dual_domain_rootful_rehearsal
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_p1_dual_domain_rootful_gate.py
                         --run
                         --base-image
                             "${HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_IMAGE}"
                         --expected-source-commit
                             "${HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT}"
                         --report
                             ${CMAKE_BINARY_DIR}/p1-dual-domain-rootful-rehearsal.json)
        set_tests_properties(
            hepta_p1_dual_domain_rootful_rehearsal PROPERTIES
            LABELS "explicit-rehearsal;non-certifying;systemd;watch;paper;offline"
            PASS_REGULAR_EXPRESSION "REHEARSAL_ONLY"
            RUN_SERIAL TRUE
            RESOURCE_LOCK hepta_p1_dual_domain_rootful_rehearsal
            TIMEOUT 1800
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(HEPTA_ENABLE_P1_CAMPAIGN_ROOTFUL_LIVENESS_REHEARSAL)
        add_test(NAME hepta_p1_campaign_rootful_liveness_rehearsal
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_p1_campaign_rootful_liveness_gate.py
                         --run
                         --base-image
                             "${HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_IMAGE}"
                         --expected-source-commit
                             "${HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT}"
                         --report
                             ${CMAKE_BINARY_DIR}/p1-campaign-rootful-liveness-rehearsal.json)
        set_tests_properties(
            hepta_p1_campaign_rootful_liveness_rehearsal PROPERTIES
            LABELS "explicit-rehearsal;non-certifying;systemd;p1;liveness;offline"
            PASS_REGULAR_EXPRESSION "REHEARSAL_ONLY"
            RUN_SERIAL TRUE
            RESOURCE_LOCK hepta_p1_campaign_rootful_liveness_rehearsal
            TIMEOUT 1800
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(HEPTA_ENABLE_OPENCLAW_REAL_LOADER_GATE)
        add_test(NAME hepta_openclaw_real_loader_gate
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_heptatrader_openclaw_loader_gate.py
                         --run --require)
        set_tests_properties(hepta_openclaw_real_loader_gate PROPERTIES
            LABELS "explicit-rehearsal;openclaw;offline"
            RUN_SERIAL TRUE
            TIMEOUT 180
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
    if(NOT HEPTA_AGENT_OS_SOURCE_ONLY)
        add_test(NAME hepta_evidence_ingestion_receipt_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_evidence_ingestion_receipt_tests.py)
        set_tests_properties(hepta_evidence_ingestion_receipt_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_round36_certification_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_round36_certification_tests.py)
        set_tests_properties(hepta_round36_certification_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_evidence_set_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_evidence_set_tests.py)
        set_tests_properties(hepta_evidence_set_tests PROPERTIES
            RUN_SERIAL TRUE
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_release_validation_closure_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_release_validation_closure_tests.py)
        set_tests_properties(
            hepta_release_validation_closure_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_vendor_overlay_set_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_vendor_overlay_set_tests.py)
        set_tests_properties(hepta_vendor_overlay_set_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_runtime_package_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_runtime_package_tests.py)
        set_tests_properties(hepta_runtime_package_tests PROPERTIES
            TIMEOUT 900
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_distribution_artifact_set_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_distribution_artifact_set_tests.py)
        set_tests_properties(hepta_distribution_artifact_set_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_delivery_closure_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_delivery_closure_tests.py)
        set_tests_properties(hepta_delivery_closure_tests PROPERTIES
            TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
        add_test(NAME hepta_source_baseline_tests
                 COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/heptatrader_source_baseline_tests.py)
        set_tests_properties(hepta_source_baseline_tests PROPERTIES
            TIMEOUT 30
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()

    if(HEPTA_ENABLE_CONTAINERIZED_SYSTEMD_REHEARSAL)
        if(NOT HEPTA_ENABLE_IBAPI)
            message(FATAL_ERROR
                "rootful systemd pre-gate must be configured in an IBAPI-on tree")
        endif()
        if(HEPTA_REHEARSAL_IB_DISABLED_BUILD_DIR STREQUAL "")
            message(FATAL_ERROR
                "HEPTA_REHEARSAL_IB_DISABLED_BUILD_DIR is required")
        endif()
        if(NOT HEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE MATCHES
                "^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]+$")
            message(FATAL_ERROR
                "HEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE must be digest-pinned")
        endif()
        string(REGEX REPLACE "^.*@sha256:" "" HEPTA_ROOTFUL_BASE_DIGEST
               "${HEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE}")
        string(LENGTH "${HEPTA_ROOTFUL_BASE_DIGEST}"
               HEPTA_ROOTFUL_BASE_DIGEST_LENGTH)
        if(NOT HEPTA_ROOTFUL_BASE_DIGEST_LENGTH EQUAL 64)
            message(FATAL_ERROR
                "HEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE digest must be 64 hex characters")
        endif()
        add_test(NAME hepta_execution_containerized_systemd_rehearsal_tests
            COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/scripts/run_hepta_execution_rootful_systemd_gate.py
                --ibapi-build-dir ${CMAKE_BINARY_DIR}
                --ib-disabled-build-dir
                    ${HEPTA_REHEARSAL_IB_DISABLED_BUILD_DIR}
                --base-image ${HEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE}
                --report
                    ${CMAKE_BINARY_DIR}/execution-rootful-systemd-gate.json)
        set_tests_properties(
            hepta_execution_containerized_systemd_rehearsal_tests PROPERTIES
            LABELS "containerized-rehearsal;systemd;offline"
            RUN_SERIAL TRUE
            RESOURCE_LOCK hepta_containerized_systemd_rehearsal
            TIMEOUT 6000)
    endif()
endif()

add_test(NAME hepta_agent_os_unit_hardening_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_hepta_agent_os_units.py)

add_test(NAME hepta_agent_trust_domain_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_agent_trust_domain_tests.py)

add_test(NAME hepta_broker_egress_policy_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_broker_egress_policy_tests.py)
set_tests_properties(hepta_broker_egress_policy_tests PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_broker_network_policy_gate
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_hepta_broker_network_policy.py)
set_tests_properties(hepta_broker_network_policy_gate PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_ib_paper_domain_authority_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_ib_paper_domain_authority_tests.py)
set_tests_properties(hepta_ib_paper_domain_authority_tests PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_ib_paper_campaign_operator_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_ib_paper_campaign_operator_tests.py)
set_tests_properties(hepta_ib_paper_campaign_operator_tests PROPERTIES
    TIMEOUT 120
    RUN_SERIAL TRUE
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_paper_receipt_contracts_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_paper_receipt_contracts_tests.py)
set_tests_properties(hepta_paper_receipt_contracts_tests PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_p1_paper_canary_executor_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_canary_executor_tests.py)
set_tests_properties(hepta_p1_paper_canary_executor_tests PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_p1_paper_canary_launch_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_canary_launch_tests.py)
set_tests_properties(hepta_p1_paper_canary_launch_tests PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_p1_paper_canary_root_finalizer_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_canary_root_finalizer_tests.py)
set_tests_properties(hepta_p1_paper_canary_root_finalizer_tests PROPERTIES
    TIMEOUT 60
    RUN_SERIAL TRUE
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_p1_paper_terminal_witness_verifier_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_p1_paper_terminal_witness_verifier_tests.py)
set_tests_properties(hepta_p1_paper_terminal_witness_verifier_tests PROPERTIES
    TIMEOUT 60
    RUN_SERIAL TRUE
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

add_test(NAME hepta_local_paper_atomic_replace_sandbox_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_local_paper_atomic_replace_sandbox_tests.py)
set_tests_properties(hepta_local_paper_atomic_replace_sandbox_tests PROPERTIES
    TIMEOUT 90
    RUN_SERIAL TRUE
    ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")

find_program(HEPTA_SYSTEMD_ANALYZE_EXECUTABLE systemd-analyze)
if(HEPTA_SYSTEMD_ANALYZE_EXECUTABLE)
    add_test(NAME hepta_agent_os_systemd_verify_tests
             COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_hepta_agent_os_units.py
                     --systemd-verify-only)
endif()

add_executable(hepta_agent_os_runtime_config_tests
    agent_os_runtime_config_tests.cpp
    ../HeptaTrade/tool_host/agent_os_runtime_config.cpp
)
target_include_directories(hepta_agent_os_runtime_config_tests PRIVATE
    ../HeptaTrade
)
add_test(NAME hepta_agent_os_runtime_config_tests
         COMMAND hepta_agent_os_runtime_config_tests)

add_executable(hepta_agent_os_runtime_composition_tests
    agent_os_runtime_composition_tests.cpp
)
target_include_directories(hepta_agent_os_runtime_composition_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_agent_os_runtime_composition_tests
    hepta_agent_os_core hepta_execution_core)
add_test(NAME hepta_agent_os_runtime_composition_tests
         COMMAND hepta_agent_os_runtime_composition_tests)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_test(NAME hepta_tool_gateway_forbidden_symbols_tests
        COMMAND ${CMAKE_COMMAND}
            -DHEPTA_GATEWAY_BINARY=$<TARGET_FILE:hepta_tool_gatewayd>
            -DHEPTA_NM_EXECUTABLE=${CMAKE_NM}
            -DHEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET=$<IF:$<CONFIG:Release>,ON,OFF>
            -P ${CMAKE_SOURCE_DIR}/cmake/verify_gateway_forbidden_symbols.cmake)
endif()

add_executable(hepta_heptactl_tests
    heptactl_tests.cpp
    ../HeptaTrade/cli/heptactl_command.cpp
    ../HeptaTrade/cli/heptactl_exit_codes.cpp
    ../HeptaTrade/tool_host/typed_tool_framing.cpp
    ../HeptaTrade/tool_host/typed_tool_protocol.cpp
    ../HeptaTrade/tool_host/typed_tool_result_codec.cpp
)
target_include_directories(hepta_heptactl_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_heptactl_tests hepta_trading_tool_core)
add_test(NAME hepta_heptactl_tests COMMAND hepta_heptactl_tests)
add_test(NAME hepta_heptactl_response_boundary_fixture_tests
    COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/heptactl_response_boundary_fixture.py
        --heptactl $<TARGET_FILE:heptactl>)

add_executable(hepta_sessionctl_tests
    hepta_sessionctl_tests.cpp
    ../HeptaTrade/cli/hepta_sessionctl_command.cpp
    ../HeptaTrade/cli/hepta_sessionctl_terminal_cleanup.cpp
    ../HeptaTrade/tool_host/session_supervisor_lease_store.cpp
    ../HeptaTrade/tool_host/session_supervisor_protocol.cpp
)
target_include_directories(hepta_sessionctl_tests PRIVATE ../HeptaTrade)
target_link_libraries(hepta_sessionctl_tests pthread OpenSSL::Crypto)
add_test(NAME hepta_sessionctl_tests COMMAND hepta_sessionctl_tests)

add_executable(hepta_tool_gateway_session_policy_tests
    tool_gateway_session_policy_tests.cpp
    ../HeptaTrade/tool_host/tool_gateway_session_policy.cpp
    ../HeptaTrade/tool_host/agent_os_runtime_config.cpp
    ../HeptaTrade/tool_host/execution_gateway_runtime_config.cpp
)
target_include_directories(hepta_tool_gateway_session_policy_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib)
target_link_libraries(hepta_tool_gateway_session_policy_tests pthread)
add_test(NAME hepta_tool_gateway_session_policy_tests
         COMMAND hepta_tool_gateway_session_policy_tests)

add_executable(hepta_tool_gateway_runtime_composition_tests
    tool_gateway_runtime_composition_tests.cpp
    ${HEPTA_EXECUTION_SERVICE_RUNTIME_SOURCES}
    ../HeptaTrade/simulator/deterministic_execution_venue.cpp
    ../HeptaTrade/tool_host/unix_session_supervisor_client.cpp
)
target_include_directories(hepta_tool_gateway_runtime_composition_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib)
target_link_libraries(hepta_tool_gateway_runtime_composition_tests
    hepta_agent_os_core hepta_execution_core hepta_native_tool_client)
add_test(NAME hepta_tool_gateway_runtime_composition_tests
         COMMAND hepta_tool_gateway_runtime_composition_tests)

add_executable(hepta_ib_paper_agent_tool_e2e_tests
    ib_paper_agent_tool_e2e_tests.cpp
    ${HEPTA_IB_PAPER_RUNTIME_SOURCES}
    ${HEPTA_IB_PAPER_POLICY_SOURCES}
    ${HEPTA_IB_ADAPTER_TRANSPORT_SOURCES}
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    ../HeptaTrade/adapter_ib/ib_order_lifecycle.cpp
    ../HeptaTrade/adapter_ib/ib_venue_correlation.cpp
    ../HeptaTrade/risk/pre_trade_risk_engine.cpp
    ../HeptaTrade/state/authoritative_trading_snapshot_store.cpp
    ../HeptaTrade/state/ib_authoritative_quote_subscription_set.cpp
    ../HeptaTrade/tool_host/unix_session_supervisor_client.cpp
)
target_include_directories(hepta_ib_paper_agent_tool_e2e_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib)
target_link_libraries(hepta_ib_paper_agent_tool_e2e_tests
    hepta_agent_os_core hepta_execution_core hepta_native_tool_client)
set_target_properties(hepta_ib_paper_agent_tool_e2e_tests PROPERTIES
    CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
if(MSVC)
    target_compile_options(hepta_ib_paper_agent_tool_e2e_tests PRIVATE /W4 /WX)
else()
    target_compile_options(hepta_ib_paper_agent_tool_e2e_tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME hepta_ib_paper_agent_tool_e2e_tests
         COMMAND hepta_ib_paper_agent_tool_e2e_tests)
set_tests_properties(hepta_ib_paper_agent_tool_e2e_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 60)

add_test(NAME hepta_mcp_adapter_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/hepta_mcp_adapter_tests.py)

add_test(NAME hepta_no_direct_broker_paths_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/check_no_direct_broker_paths_tests.py)
add_test(NAME hepta_no_direct_broker_paths_gate
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_no_direct_broker_paths.py
                 --root ${CMAKE_CURRENT_SOURCE_DIR}/..)
add_test(NAME hepta_shadow_host_installer_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_shadow_host_installer_tests.py)
add_test(NAME hepta_official_source_capture_tests
         COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                 ${CMAKE_CURRENT_SOURCE_DIR}/hepta_official_source_capture_tests.py)
if(NOT HEPTA_AGENT_OS_SOURCE_ONLY)
    add_test(NAME hepta_versioned_source_baseline_gate
        COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_source_baseline.py
                --root ${CMAKE_CURRENT_SOURCE_DIR}/..
                --baseline ${CMAKE_CURRENT_SOURCE_DIR}/../source-baseline.json
                --expected-version 0.1.0-beta.1-round122)
endif()

add_executable(hepta_execution_service_process_e2e_tests
    execution_service_process_e2e_tests.cpp
    ../HeptaTrade/simulator/deterministic_execution_venue.cpp
)
target_include_directories(hepta_execution_service_process_e2e_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_execution_service_process_e2e_tests
    hepta_trading_tool_core hepta_execution_core)
add_test(NAME hepta_execution_service_process_e2e_tests
         COMMAND hepta_execution_service_process_e2e_tests)

add_executable(hepta_execution_service_runtime_config_tests
    execution_service_runtime_config_tests.cpp
    ../HeptaTrade/execution/execution_service_runtime_config.cpp
)
target_include_directories(hepta_execution_service_runtime_config_tests PRIVATE
    ../HeptaTrade
)
add_test(NAME hepta_execution_service_runtime_config_tests
         COMMAND hepta_execution_service_runtime_config_tests)

add_executable(hepta_execution_service_runtime_composition_tests
    execution_service_runtime_composition_tests.cpp
    ${HEPTA_EXECUTION_SERVICE_RUNTIME_SOURCES}
    ../HeptaTrade/simulator/deterministic_execution_venue.cpp
)
target_include_directories(hepta_execution_service_runtime_composition_tests PRIVATE
    ../HeptaTrade
    ../HeptaTrade/adapter_ib
)
target_link_libraries(hepta_execution_service_runtime_composition_tests hepta_execution_core)
add_test(NAME hepta_execution_service_runtime_composition_tests
         COMMAND hepta_execution_service_runtime_composition_tests)

# Explicit-only rootful systemd probes. They are compiled with the offline test
# closure but are never registered with CTest and are never installed. The
# client exposes identity/event-read operations only; the sandbox probe links
# the production kill-switch reader but deliberately links no IB SDK or adapter.
set(HEPTA_EXECUTION_SYSTEMD_PROBE_TARGETS)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(hepta_execution_systemd_client_probe
        execution_systemd_client_probe.cpp
    )
    target_include_directories(hepta_execution_systemd_client_probe PRIVATE
        ../HeptaTrade
        ../HeptaTrade/adapter_ib
    )
    target_link_libraries(hepta_execution_systemd_client_probe
        hepta_execution_client)

    add_executable(hepta_execution_systemd_sandbox_probe
        execution_systemd_sandbox_probe.cpp
        ../HeptaTrade/execution/ib_paper_kill_switch.cpp
    )
    target_include_directories(hepta_execution_systemd_sandbox_probe PRIVATE
        ../HeptaTrade
    )
    target_link_libraries(hepta_execution_systemd_sandbox_probe pthread)

    foreach(probe_target IN ITEMS
            hepta_execution_systemd_client_probe
            hepta_execution_systemd_sandbox_probe)
        set_target_properties(${probe_target} PROPERTIES
            CXX_STANDARD 11
            CXX_STANDARD_REQUIRED ON
            CXX_EXTENSIONS OFF)
        if(MSVC)
            target_compile_options(${probe_target} PRIVATE /W4 /WX)
        else()
            target_compile_options(${probe_target} PRIVATE
                -Wall -Wextra -Wpedantic -Werror)
        endif()
    endforeach()
    list(APPEND HEPTA_EXECUTION_SYSTEMD_PROBE_TARGETS
        hepta_execution_systemd_client_probe
        hepta_execution_systemd_sandbox_probe)
endif()

add_test(NAME hepta_execution_gateway_short_soak
    COMMAND ${HEPTA_PYTHON3_EXECUTABLE}
        ${CMAKE_SOURCE_DIR}/scripts/run_execution_gateway_soak.py
        --build-dir ${CMAKE_BINARY_DIR}
        --soak-profile ${HEPTA_SOAK_PROFILE}
        --timeout-sec 120
        --report ${CMAKE_BINARY_DIR}/execution-gateway-short-soak.json)
if(HEPTA_SOAK_PROFILE STREQUAL "pr-smoke")
    # The PR profile preserves the former two-round short-gate bound.
    set(HEPTA_SOAK_CTEST_TIMEOUT 600)
else()
    # Release/nightly run eight rounds through the same CTest test. Keep a
    # single bounded test while allowing the expanded profile to finish on a
    # busy shared runner.
    set(HEPTA_SOAK_CTEST_TIMEOUT 1800)
endif()
set_tests_properties(hepta_execution_gateway_short_soak PROPERTIES
    RUN_SERIAL TRUE
    TIMEOUT ${HEPTA_SOAK_CTEST_TIMEOUT})

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(hepta_paper_terminal_external_latch_tests
        paper_terminal_external_latch_tests.cpp
        ${HEPTA_PAPER_TERMINAL_EXTERNAL_LATCH_SOURCES}
    )
    target_include_directories(hepta_paper_terminal_external_latch_tests PRIVATE
        ../HeptaTrade)
    target_compile_definitions(hepta_paper_terminal_external_latch_tests PRIVATE
        HEPTA_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
        HEPTA_TERMINAL_COMMITTER_PATH="$<TARGET_FILE:hepta_paper_terminal_latch_committer>")
    target_link_libraries(hepta_paper_terminal_external_latch_tests
        OpenSSL::Crypto)
    add_dependencies(hepta_paper_terminal_external_latch_tests
        hepta_paper_terminal_latch_committer)
    set_target_properties(hepta_paper_terminal_external_latch_tests PROPERTIES
        CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
    if(MSVC)
        target_compile_options(hepta_paper_terminal_external_latch_tests PRIVATE
            /W4 /WX)
    else()
        target_compile_options(hepta_paper_terminal_external_latch_tests PRIVATE
            -Wall -Wextra -Wpedantic -Werror)
    endif()
    add_test(NAME hepta_paper_terminal_external_latch_tests
        COMMAND hepta_paper_terminal_external_latch_tests)
    set_tests_properties(hepta_paper_terminal_external_latch_tests PROPERTIES
        RUN_SERIAL TRUE TIMEOUT 30)
endif()

# These characterization tests intentionally use assert() for compactness.
# Release builds define NDEBUG globally, so explicitly keep assertions and
# their evaluated expressions alive for every test executable.
set(HEPTA_ASSERT_TEST_TARGETS
    hepta_trading_contract_tests
    hepta_native_tool_client_tests
    hepta_execution_coordinator_tests
    hepta_oms_journal_durability_tests
    hepta_trading_tool_registry_tests
    hepta_execution_event_hub_tests
    hepta_execution_event_feed_tests
    hepta_execution_gateway_runtime_composition_tests
    hepta_trading_tool_host_tests
    hepta_unix_tool_server_tests
    hepta_unix_session_supervisor_server_tests
    hepta_session_supervisor_lease_store_migration_tests
    hepta_agent_simulator_e2e_tests
    hepta_decision_lease_manager_tests
    hepta_execution_decision_lease_authority_tests
    hepta_authoritative_trading_snapshot_store_tests
    hepta_snapshot_refresh_coordinator_tests
    hepta_ib_authoritative_event_queue_tests
    hepta_ib_order_lifecycle_tests
    hepta_ib_paper_kill_switch_tests
    hepta_ib_paper_execution_profile_tests
    hepta_ib_paper_execution_runtime_config_tests
    hepta_ib_paper_execution_runtime_composition_tests
    hepta_ib_paper_execution_process_e2e_tests
    hepta_ib_authoritative_order_projector_tests
    hepta_ib_authoritative_account_position_consumer_tests
    hepta_ib_authoritative_open_order_consumer_tests
    hepta_ib_authoritative_quote_subscription_set_tests
    hepta_ib_authoritative_recovery_coordinator_tests
    hepta_trading_tool_session_catalog_tests
    hepta_ib_authoritative_recovery_event_consumer_tests
    hepta_ib_connection_lifecycle_state_machine_tests
    hepta_agent_os_runtime_config_tests
    hepta_agent_os_runtime_composition_tests
    hepta_heptactl_tests
    hepta_sessionctl_tests
    hepta_tool_gateway_session_policy_tests
    hepta_tool_gateway_runtime_composition_tests
    hepta_ib_paper_agent_tool_e2e_tests
    hepta_execution_service_process_e2e_tests
    hepta_execution_service_runtime_config_tests
    hepta_execution_service_runtime_composition_tests
)
if(TARGET hepta_paper_terminal_external_latch_tests)
    list(APPEND HEPTA_ASSERT_TEST_TARGETS
        hepta_paper_terminal_external_latch_tests)
endif()

add_custom_target(hepta_agent_os_test_binaries
    DEPENDS ${HEPTA_ASSERT_TEST_TARGETS}
        ${HEPTA_EXECUTION_SYSTEMD_PROBE_TARGETS}
        heptactl hepta_sessionctl hepta_tool_gatewayd
        hepta_executiond hepta_ib_executiond
)

foreach(test_target IN LISTS HEPTA_ASSERT_TEST_TARGETS)
    if(MSVC)
        target_compile_options(${test_target} PRIVATE /UNDEBUG)
    else()
        target_compile_options(${test_target} PRIVATE -UNDEBUG)
    endif()
endforeach()
