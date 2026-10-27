find_package(OpenSSL REQUIRED)
find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(HEPTA_CORE_TEST_TARGETS)

function(hepta_register_core_test target)
    target_include_directories(${target} PRIVATE
        ../HeptaTrade
        ../HeptaTrade/adapter_ib)
    set_target_properties(${target} PROPERTIES
        CXX_STANDARD 11
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    # The historical tests intentionally use assert() as both an invariant and
    # an expression-evaluation boundary (for example assert(server.Start())).
    # Release/RelWithDebInfo define NDEBUG globally, which would erase those
    # calls and turn the suite into undefined follow-on behavior. Keep test
    # semantics independent of the production optimization configuration.
    if(MSVC)
        target_compile_options(${target} PRIVATE /UNDEBUG)
    else()
        target_compile_options(${target} PRIVATE -UNDEBUG)
    endif()
    if(ARGC GREATER 1)
        add_test(NAME ${target} COMMAND ${ARGN})
    else()
        add_test(NAME ${target} COMMAND ${target})
    endif()
    set_tests_properties(${target} PROPERTIES LABELS "core")
    list(APPEND HEPTA_CORE_TEST_TARGETS ${target})
    set(HEPTA_CORE_TEST_TARGETS "${HEPTA_CORE_TEST_TARGETS}" PARENT_SCOPE)
endfunction()

add_executable(hepta_snowball_feed_trace_tests snowball_feed_trace_tests.cpp)
target_link_libraries(hepta_snowball_feed_trace_tests PRIVATE hepta_options_core)
hepta_register_core_test(hepta_snowball_feed_trace_tests)

add_executable(hepta_options_oms_economics_audit_tests options_oms_economics_audit_tests.cpp)
target_link_libraries(hepta_options_oms_economics_audit_tests PRIVATE hepta_options_oms_audit hepta_execution_core)
hepta_register_core_test(hepta_options_oms_economics_audit_tests)

add_executable(hepta_zero_dte_execution_fault_tests zero_dte_execution_fault_tests.cpp)
target_link_libraries(hepta_zero_dte_execution_fault_tests PRIVATE hepta_execution_core hepta_options_core)
hepta_register_core_test(hepta_zero_dte_execution_fault_tests)

add_test(NAME hepta_options_shadow_runner_tests
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/options_shadow_runner_tests.py)
set_tests_properties(hepta_options_shadow_runner_tests PROPERTIES LABELS "core;options")

add_executable(hepta_ib_observation_refresh_tests ib_observation_refresh_tests.cpp)
hepta_register_core_test(hepta_ib_observation_refresh_tests)

add_executable(hepta_snowball_option_feed_tests snowball_option_feed_tests.cpp)
target_link_libraries(hepta_snowball_option_feed_tests PRIVATE hepta_options_core)
hepta_register_core_test(hepta_snowball_option_feed_tests)

add_executable(hepta_zero_dte_boundary_tests zero_dte_boundary_tests.cpp)
target_link_libraries(hepta_zero_dte_boundary_tests PRIVATE hepta_options_core)
hepta_register_core_test(hepta_zero_dte_boundary_tests)

add_executable(hepta_zero_dte_tests zero_dte_tests.cpp)
target_link_libraries(hepta_zero_dte_tests PRIVATE hepta_options_core hepta_oms_journal)
hepta_register_core_test(hepta_zero_dte_tests)
add_executable(hepta_ib_recent_execution_aggregation_tests
    ib_recent_execution_aggregation_tests.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_recent_orders.cpp)
target_link_libraries(hepta_ib_recent_execution_aggregation_tests PRIVATE
    hepta_ib_paper_policy hepta_ib_adapter_runtime hepta_options_core)
hepta_register_core_test(hepta_ib_recent_execution_aggregation_tests)

add_executable(hepta_trading_contract_tests
    trading_contract_tests.cpp)
hepta_register_core_test(hepta_trading_contract_tests)

add_executable(hepta_native_tool_client_tests
    native_tool_client_tests.cpp
    ../HeptaTrade/tool_host/unix_session_supervisor_client.cpp)
target_link_libraries(hepta_native_tool_client_tests
    hepta_native_tool_client hepta_session_supervisor_core)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_link_options(hepta_native_tool_client_tests PRIVATE -Wl,--wrap=socket)
    target_compile_definitions(hepta_native_tool_client_tests PRIVATE HEPTA_TEST_WRAP_SOCKET=1)
endif()
hepta_register_core_test(hepta_native_tool_client_tests)

add_executable(hepta_pnl_read_stream_tests
    heptactl_pnl_stream_tests.cpp
    ../HeptaTrade/cli/heptactl_pnl_stream.cpp
    ../HeptaTrade/cli/heptactl_exit_codes.cpp)
target_link_libraries(hepta_pnl_read_stream_tests PRIVATE hepta_native_tool_client)
hepta_register_core_test(hepta_pnl_read_stream_tests)

add_executable(hepta_execution_coordinator_tests
    execution_coordinator_tests.cpp
    execution_coordinator_test_support.cpp
    execution_coordinator_placement_recovery_tests.cpp
    execution_coordinator_flatten_tests.cpp
    execution_coordinator_session_concurrency_tests.cpp
    compat/oms_recover.cpp)
target_link_libraries(hepta_execution_coordinator_tests
    hepta_execution_core)
target_compile_definitions(hepta_execution_coordinator_tests PRIVATE
    HEPTA_SOURCE_ROOT="${CMAKE_SOURCE_DIR}")
hepta_register_core_test(hepta_execution_coordinator_tests)

add_executable(hepta_oms_journal_durability_tests
    oms_journal_durability_tests.cpp)
target_link_libraries(hepta_oms_journal_durability_tests PRIVATE hepta_oms_journal)
hepta_register_core_test(hepta_oms_journal_durability_tests)

add_executable(hepta_oms_journal_schema_v4_tests
    oms_journal_schema_v4_tests.cpp)
target_link_libraries(hepta_oms_journal_schema_v4_tests PRIVATE hepta_oms_journal)
hepta_register_core_test(hepta_oms_journal_schema_v4_tests)

# The allocator-fault sweep runs the same production objects as the ordinary
# coordinator tests, including the selected sanitizer profile. Its Python
# wrapper owns only a disposable journal directory, never another compiler.
add_executable(hepta_recovery_projection_faults recovery_projection_faults.cpp)
target_link_libraries(hepta_recovery_projection_faults PRIVATE hepta_execution_core)
hepta_register_core_test(hepta_recovery_projection_faults
    ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/recovery_projection_runner.py
    $<TARGET_FILE:hepta_recovery_projection_faults>)
set_tests_properties(hepta_recovery_projection_faults PROPERTIES
    LABELS "core;storage" TIMEOUT 90)

add_executable(hepta_trading_tool_registry_tests
    trading_tool_registry_tests.cpp)
target_link_libraries(hepta_trading_tool_registry_tests
    hepta_trading_tool_core hepta_execution_core)
hepta_register_core_test(hepta_trading_tool_registry_tests)

add_executable(hepta_execution_event_hub_tests
    execution_event_hub_tests.cpp)
target_link_libraries(hepta_execution_event_hub_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_event_hub_tests)

add_executable(hepta_execution_event_feed_tests
    execution_event_feed_tests.cpp)
target_link_libraries(hepta_execution_event_feed_tests
    hepta_execution_core hepta_execution_event_relay)
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
    unix_tool_server_tests.cpp)
target_link_libraries(hepta_unix_tool_server_tests
    hepta_agent_os_core hepta_execution_core hepta_native_tool_client)
add_dependencies(hepta_unix_tool_server_tests heptactl)
target_compile_definitions(hepta_unix_tool_server_tests PRIVATE
    HEPTACTL_BINARY="$<TARGET_FILE:heptactl>")
hepta_register_core_test(hepta_unix_tool_server_tests)
set_tests_properties(hepta_unix_tool_server_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_unix_session_supervisor_server_tests
    unix_session_supervisor_server_tests.cpp
    unix_session_supervisor_test_support.cpp
    unix_session_supervisor_protocol_audit_tests.cpp
    unix_session_supervisor_lifecycle_tests.cpp
    unix_session_supervisor_generation_watch_tests.cpp
    unix_session_supervisor_terminal_control_tests.cpp)
target_precompile_headers(hepta_unix_session_supervisor_server_tests PRIVATE
    unix_session_supervisor_test_prelude.h)
target_link_libraries(hepta_unix_session_supervisor_server_tests
    hepta_agent_os_core hepta_execution_core)
add_dependencies(hepta_unix_session_supervisor_server_tests hepta_sessionctl)
target_compile_definitions(hepta_unix_session_supervisor_server_tests PRIVATE
    HEPTA_SESSIONCTL_TEST_BINARY="$<TARGET_FILE:hepta_sessionctl>")
hepta_register_core_test(hepta_unix_session_supervisor_server_tests)
set_tests_properties(hepta_unix_session_supervisor_server_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_session_supervisor_lease_store_migration_tests
    session_supervisor_lease_store_migration_tests.cpp)
target_link_libraries(hepta_session_supervisor_lease_store_migration_tests
    PRIVATE hepta_session_supervisor_core)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_link_options(hepta_session_supervisor_lease_store_migration_tests
        PRIVATE -Wl,--wrap=fsync -Wl,--wrap=rename)
endif()
hepta_register_core_test(hepta_session_supervisor_lease_store_migration_tests)

add_executable(hepta_gateway_catalog_tests gateway_catalog_tests.cpp)
target_link_libraries(hepta_gateway_catalog_tests PRIVATE hepta_session_supervisor_core)
add_dependencies(hepta_gateway_catalog_tests hepta_gateway_catalog)
target_compile_definitions(hepta_gateway_catalog_tests PRIVATE
    HEPTA_CATALOG_BINARY="$<TARGET_FILE:hepta_gateway_catalog>")
hepta_register_core_test(hepta_gateway_catalog_tests)

add_executable(hepta_sessionctl_owner_audit_tests
    sessionctl_owner_audit_tests.cpp
    ../HeptaTrade/cli/hepta_sessionctl_owner_audit.cpp
    ../HeptaTrade/cli/hepta_sessionctl_command.cpp
    ../HeptaTrade/tool_host/agent_os_runtime_config.cpp
    ../HeptaTrade/tool_host/execution_gateway_runtime_config.cpp)
target_link_libraries(hepta_sessionctl_owner_audit_tests PRIVATE
    hepta_session_audit hepta_session_supervisor_core
    hepta_execution_client hepta_execution_server hepta_agent_execution_support
    Threads::Threads OpenSSL::Crypto ZLIB::ZLIB)
hepta_register_core_test(hepta_sessionctl_owner_audit_tests)
set_tests_properties(hepta_sessionctl_owner_audit_tests PROPERTIES TIMEOUT 30)

add_executable(hepta_sessionctl_inactive_owner_audit_tests
    sessionctl_inactive_owner_audit_tests.cpp
    execution_client_sender_credentials_tests.cpp
    execution_client_ancillary_tests.cpp
    ../HeptaTrade/cli/hepta_sessionctl_inactive_owner_audit.cpp
    ../HeptaTrade/tool_host/agent_os_runtime_config.cpp
    ../HeptaTrade/tool_host/execution_gateway_runtime_config.cpp)
target_link_libraries(hepta_sessionctl_inactive_owner_audit_tests PRIVATE
    hepta_session_audit hepta_session_supervisor_core hepta_typed_tool_protocol
    hepta_execution_client hepta_execution_server hepta_execution_core hepta_agent_execution_support
    Threads::Threads OpenSSL::Crypto ZLIB::ZLIB)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_link_options(hepta_sessionctl_inactive_owner_audit_tests PRIVATE -Wl,--wrap=recvmsg)
endif()
hepta_register_core_test(hepta_sessionctl_inactive_owner_audit_tests)
set_tests_properties(hepta_sessionctl_inactive_owner_audit_tests PROPERTIES TIMEOUT 60)


add_executable(hepta_agent_simulator_e2e_tests
    agent_simulator_e2e_tests.cpp)
target_link_libraries(hepta_agent_simulator_e2e_tests
    hepta_agent_os_core hepta_execution_core hepta_simulator_runtime
    hepta_authoritative_state)
hepta_register_core_test(hepta_agent_simulator_e2e_tests)
set_tests_properties(hepta_agent_simulator_e2e_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 120)

add_executable(hepta_simulator_risk_runtime_tests simulator_risk_runtime_tests.cpp)
target_link_libraries(hepta_simulator_risk_runtime_tests hepta_execution_service_runtime)
hepta_register_core_test(hepta_simulator_risk_runtime_tests)
set_tests_properties(hepta_simulator_risk_runtime_tests PROPERTIES
    RUN_SERIAL TRUE TIMEOUT 30)

add_executable(hepta_decision_lease_manager_tests
    decision_lease_manager_tests.cpp)
target_link_libraries(hepta_decision_lease_manager_tests
    hepta_agent_execution_support)
hepta_register_core_test(hepta_decision_lease_manager_tests)

add_executable(hepta_execution_decision_lease_authority_tests
    execution_decision_lease_authority_tests.cpp)
target_link_libraries(hepta_execution_decision_lease_authority_tests
    hepta_execution_core)
hepta_register_core_test(hepta_execution_decision_lease_authority_tests)

add_executable(hepta_authoritative_trading_snapshot_store_tests
    authoritative_trading_snapshot_store_tests.cpp)
target_link_libraries(hepta_authoritative_trading_snapshot_store_tests
    hepta_authoritative_state)
hepta_register_core_test(hepta_authoritative_trading_snapshot_store_tests)

add_executable(hepta_snapshot_refresh_coordinator_tests
    snapshot_refresh_coordinator_tests.cpp)
target_link_libraries(hepta_snapshot_refresh_coordinator_tests
    hepta_authoritative_state)
hepta_register_core_test(hepta_snapshot_refresh_coordinator_tests)

add_executable(hepta_ib_order_lifecycle_tests
    ib_order_lifecycle_tests.cpp)
target_link_libraries(hepta_ib_order_lifecycle_tests PRIVATE hepta_ib_order_state)
hepta_register_core_test(hepta_ib_order_lifecycle_tests)

add_executable(hepta_ib_paper_kill_switch_tests
    ib_paper_kill_switch_tests.cpp)
target_link_libraries(hepta_ib_paper_kill_switch_tests PRIVATE hepta_ib_paper_policy)
hepta_register_core_test(hepta_ib_paper_kill_switch_tests)

add_executable(hepta_ib_paper_execution_profile_tests ib_paper_execution_profile_tests.cpp
    ../HeptaTrade/execution/ib_paper_execution_runtime_config.cpp)
target_link_libraries(hepta_ib_paper_execution_profile_tests PRIVATE hepta_ib_paper_policy)
hepta_register_core_test(hepta_ib_paper_execution_profile_tests)

add_executable(hepta_ib_live_terminal_reconciliation_tests
    ib_live_terminal_reconciliation_tests.cpp)
target_link_libraries(hepta_ib_live_terminal_reconciliation_tests
    hepta_execution_core hepta_risk_engine hepta_ib_adapter_runtime)
hepta_register_core_test(hepta_ib_live_terminal_reconciliation_tests)

add_executable(hepta_pre_trade_risk_engine_tests
    pre_trade_risk_engine_tests.cpp)
target_link_libraries(hepta_pre_trade_risk_engine_tests PRIVATE hepta_risk_engine)
hepta_register_core_test(hepta_pre_trade_risk_engine_tests)

add_executable(hepta_portfolio_risk_snapshot_builder_tests
    portfolio_risk_snapshot_builder_tests.cpp)
target_link_libraries(hepta_portfolio_risk_snapshot_builder_tests PRIVATE hepta_risk_engine)
hepta_register_core_test(hepta_portfolio_risk_snapshot_builder_tests)

add_executable(hepta_venue_capability_tests
    venue_capability_tests.cpp)
target_link_libraries(hepta_venue_capability_tests PRIVATE
    hepta_venue_capability_adapters)
hepta_register_core_test(hepta_venue_capability_tests)

add_executable(hepta_execution_ipc_scheduling_tests execution_ipc_scheduling_tests.cpp)
target_link_libraries(hepta_execution_ipc_scheduling_tests PRIVATE hepta_execution_core)
hepta_register_core_test(hepta_execution_ipc_scheduling_tests)
set_tests_properties(hepta_execution_ipc_scheduling_tests PROPERTIES TIMEOUT 30)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(hepta_audit_journal_lifecycle_tests audit_journal_lifecycle_tests.cpp)
    target_link_libraries(hepta_audit_journal_lifecycle_tests PRIVATE hepta_session_audit)
    target_link_options(hepta_audit_journal_lifecycle_tests PRIVATE
        -Wl,--wrap=fsync -Wl,--wrap=fdatasync -Wl,--wrap=rename
        -Wl,--wrap=fstat -Wl,--wrap=lstat -Wl,--wrap=inotify_init1)
    hepta_register_core_test(hepta_audit_journal_lifecycle_tests)
    set_tests_properties(hepta_audit_journal_lifecycle_tests PROPERTIES TIMEOUT 90)
endif()

add_executable(hepta_ib_admission_event_policy_tests ib_admission_event_policy_tests.cpp)
hepta_register_core_test(hepta_ib_admission_event_policy_tests)

add_executable(hepta_ib_local_market_data_request_tests ib_local_market_data_request_tests.cpp)
target_link_libraries(hepta_ib_local_market_data_request_tests PRIVATE hepta_ib_adapter_runtime)
hepta_register_core_test(hepta_ib_local_market_data_request_tests)

add_executable(hepta_ib_startup_recovery_hold_tests ib_startup_recovery_hold_tests.cpp)
hepta_register_core_test(hepta_ib_startup_recovery_hold_tests)

add_executable(hepta_ib_callback_economics_tests ib_callback_economics_tests.cpp)
target_link_libraries(hepta_ib_callback_economics_tests PRIVATE hepta_oms_journal)
hepta_register_core_test(hepta_ib_callback_economics_tests)

add_executable(hepta_ib_cumulative_execution_evidence_tests ib_cumulative_execution_evidence_tests.cpp)
hepta_register_core_test(hepta_ib_cumulative_execution_evidence_tests)

# Execute the real recent-order projection offline; the test never starts a
# service or connects to IB. Keep the production source list as the single owner.
get_target_property(_hepta_ib_replay_sources hepta_ib_executiond SOURCES)
list(REMOVE_ITEM _hepta_ib_replay_sources execution/hepta_ib_executiond.cpp)
list(TRANSFORM _hepta_ib_replay_sources PREPEND "${CMAKE_SOURCE_DIR}/HeptaTrade/")
# Compile the same offline test runtime once, with the original test-only
# assertions and usage requirements. Production and the explicit live probe
# keep their separate compilation; no installed artifact gains test flags.
add_library(hepta_ib_test_runtime_objects OBJECT ${_hepta_ib_replay_sources})
target_include_directories(hepta_ib_test_runtime_objects PRIVATE
    ../HeptaTrade ../HeptaTrade/adapter_ib)
set_target_properties(hepta_ib_test_runtime_objects PROPERTIES
    CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
if(MSVC)
    target_compile_options(hepta_ib_test_runtime_objects PRIVATE /UNDEBUG)
else()
    target_compile_options(hepta_ib_test_runtime_objects PRIVATE -UNDEBUG)
endif()
target_link_libraries(hepta_ib_test_runtime_objects PRIVATE
    hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime
    hepta_authoritative_state hepta_risk_engine)

add_executable(hepta_ib_recent_order_replay_tests ib_recent_order_replay_tests.cpp $<TARGET_OBJECTS:hepta_ib_test_runtime_objects>)
target_link_libraries(hepta_ib_recent_order_replay_tests PRIVATE
    hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime hepta_authoritative_state hepta_risk_engine)
hepta_register_core_test(hepta_ib_recent_order_replay_tests)

add_executable(hepta_ib_ingress_first_fault_tests
    ib_ingress_first_fault_tests.cpp $<TARGET_OBJECTS:hepta_ib_test_runtime_objects>)
target_link_libraries(hepta_ib_ingress_first_fault_tests PRIVATE
    hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime
    hepta_authoritative_state hepta_risk_engine)
hepta_register_core_test(hepta_ib_ingress_first_fault_tests)
set_tests_properties(hepta_ib_ingress_first_fault_tests PROPERTIES TIMEOUT 15)

# The same production runtime source list and adapter/subscription/admission
# objects; an injected wrapper has no socket and throws on every order method.
add_executable(hepta_ib_quote_recovery_component_tests
    ib_quote_recovery_component_tests.cpp $<TARGET_OBJECTS:hepta_ib_test_runtime_objects>)
target_link_libraries(hepta_ib_quote_recovery_component_tests PRIVATE
    hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime
    hepta_authoritative_state hepta_risk_engine)
hepta_register_core_test(hepta_ib_quote_recovery_component_tests)

add_executable(hepta_ib_cancel_notice_tests ib_cancel_notice_tests.cpp)
target_link_libraries(hepta_ib_cancel_notice_tests PRIVATE hepta_ib_adapter_runtime)
hepta_register_core_test(hepta_ib_cancel_notice_tests)

# Same native post-fill methods; synthetic events, no production broker or state.
add_executable(hepta_ib_postfill_reconciliation_tests
    ib_postfill_reconciliation_tests.cpp $<TARGET_OBJECTS:hepta_ib_test_runtime_objects>)
target_link_libraries(hepta_ib_postfill_reconciliation_tests PRIVATE
    hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime
    hepta_authoritative_state hepta_risk_engine)
hepta_register_core_test(hepta_ib_postfill_reconciliation_tests)

# Full runtime startup, recovery coordinator, durable OMS and real local IPC.
# Broker wrapper is socket-free; no production state or credentials are used.
add_executable(hepta_ib_full_runtime_recovery_tests
    ib_full_runtime_recovery_tests.cpp $<TARGET_OBJECTS:hepta_ib_test_runtime_objects>)
target_link_libraries(hepta_ib_full_runtime_recovery_tests PRIVATE
    hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime
    hepta_authoritative_state hepta_risk_engine hepta_typed_tool_protocol)
hepta_register_core_test(hepta_ib_full_runtime_recovery_tests)

# Deliberate local operator target only: no ordinary ctest, no installation,
# no default build, and no direct gateway access from the CLI. The runner
# pins this SDK-linked binary and places an exact frame allowlist in front.
if(HEPTA_ENABLE_IBAPI)
    add_executable(hepta_ib_quote_recovery_live_probe EXCLUDE_FROM_ALL
        ib_quote_recovery_live_probe.cpp ${_hepta_ib_replay_sources})
    target_link_libraries(hepta_ib_quote_recovery_live_probe PRIVATE
        hepta_execution_core hepta_ib_paper_policy hepta_ib_adapter_runtime
        hepta_authoritative_state hepta_risk_engine)
endif()

add_custom_target(hepta_core_test_binaries
    DEPENDS ${HEPTA_CORE_TEST_TARGETS}
        hepta_tool_gatewayd
        hepta_executiond
        hepta_ib_executiond
        heptactl
        hepta_gateway_catalog
        hepta_sessionctl)
# The validation target must link the actual production entry points, not
# merely the libraries also used by tests. This executes the Gateway's
# post-link privilege boundary in both ordinary and instrumented CI builds.
if(TARGET hepta_paper_terminal_latch_committer)
    add_dependencies(hepta_core_test_binaries hepta_paper_terminal_latch_committer)
endif()

# Historical HeptaDLL metadata is owned by the source Python partition.
# Runtime compatibility, client isolation and old-record behavior remain native tests.

# The local-operator utility is built from the same policy objects as Execution.
add_dependencies(hepta_ib_paper_execution_profile_tests hepta_ib_profile)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_test(NAME hepta_user_profile_cli_tests
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/user_profile_cli_tests.py
            $<TARGET_FILE:hepta_ib_profile>)
set_tests_properties(hepta_user_profile_cli_tests PROPERTIES LABELS "core" TIMEOUT 20)
