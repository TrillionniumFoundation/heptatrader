cmake_minimum_required(VERSION 3.21)
project(HeptaTrade LANGUAGES CXX)

find_package(OpenSSL REQUIRED)
find_package(Threads REQUIRED)

option(HEPTA_ENABLE_IBAPI
       "Build the Interactive Brokers PAPER execution daemon" OFF)
set(IBAPI_ROOT "" CACHE PATH
    "Path to the pinned IB C++ API CppClient source directory")
set(IBAPI_BIN "" CACHE PATH
    "Path to IB runtime DLLs on Windows (optional)")

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

function(hepta_runtime_target target)
    target_compile_features(${target} PUBLIC cxx_std_17)
    target_include_directories(${target} PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        target_compile_definitions(${target} PRIVATE __LINUX__=1 linux=1)
    endif()
    target_compile_options(${target} PRIVATE
        "$<$<COMPILE_LANG_AND_ID:CXX,GNU,Clang>:-Wall;-Wextra;-Wpedantic>")
    # Release Gateway binaries are link-time optimized so dead translation
    # unit helpers cannot inflate the agent-facing TCB symbol surface.  Keep
    # this scoped to GCC/Clang Linux builds; Debug/sanitizer lanes retain
    # ordinary symbols for diagnostics and the boundary check still runs.
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND
       CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(${target} PRIVATE
            "$<$<CONFIG:Release>:-flto>")
    endif()
endfunction()

set(HEPTA_EXECUTION_COORDINATOR_SOURCES
    execution/execution_coordinator.cpp
    execution/execution_coordinator_cancel.cpp
    execution/execution_coordinator_recovery.cpp
    execution/execution_coordinator_reconnect.cpp
    execution/execution_coordinator_terminal.cpp)

set(HEPTA_TOOL_HOST_CORE_SOURCES
    tool_host/trading_tool_host.cpp
    tool_host/trading_tool_host_terminal.cpp)

set(HEPTA_IB_GATEWAY_ADAPTER_SOURCES
    adapter_ib/ib_gateway_adapter.cpp
    adapter_ib/ib_gateway_adapter_event_state.cpp
    adapter_ib/ib_gateway_adapter_terminal.cpp
    adapter_ib/ib_gateway_adapter_order_submission.cpp
    adapter_ib/ib_gateway_adapter_reduce_only.cpp)

add_library(hepta_execution_contract STATIC
    execution/execution_service_protocol.cpp
    execution/execution_event_feed.cpp)
hepta_runtime_target(hepta_execution_contract)

add_library(hepta_execution_transport STATIC
    execution/execution_event_feed_transport.cpp
    execution/unix_execution_service_transport.cpp)
hepta_runtime_target(hepta_execution_transport)

add_library(hepta_execution_client STATIC
    execution/execution_event_feed_client.cpp
    execution/unix_execution_service_client.cpp
    execution/unix_execution_service_flatten_client.cpp)
hepta_runtime_target(hepta_execution_client)
target_link_libraries(hepta_execution_client PUBLIC
    hepta_execution_contract
    hepta_execution_transport
    Threads::Threads)

add_library(hepta_execution_server STATIC
    execution/execution_decision_lease_authority.cpp
    execution/execution_event_feed_server.cpp
    execution/unix_execution_service.cpp
    execution/unix_execution_service_flatten.cpp
    execution/unix_execution_service_flatten_permit.cpp)
hepta_runtime_target(hepta_execution_server)
target_link_libraries(hepta_execution_server PUBLIC
    hepta_execution_contract
    hepta_execution_transport
    Threads::Threads)

add_library(hepta_agent_execution_support STATIC
    agent/decision_lease_manager.cpp
    events/execution_event_hub.cpp
    events/owner_scoped_health_publisher.cpp)
hepta_runtime_target(hepta_agent_execution_support)
target_link_libraries(hepta_agent_execution_support PUBLIC Threads::Threads)

add_library(hepta_observability_core STATIC
    observability/runtime_telemetry.cpp)
hepta_runtime_target(hepta_observability_core)

add_library(hepta_portfolio_core STATIC
    portfolio/portfolio_compiler.cpp)
hepta_runtime_target(hepta_portfolio_core)

add_library(hepta_numeric_core STATIC
    numeric/fixed_decimal.cpp)
hepta_runtime_target(hepta_numeric_core)

add_library(hepta_marketdata_core STATIC
    marketdata/sharded_market_data.cpp)
hepta_runtime_target(hepta_marketdata_core)
target_link_libraries(hepta_marketdata_core PUBLIC
    hepta_numeric_core
    Threads::Threads
    OpenSSL::Crypto)

add_library(hepta_feature_runtime STATIC
    features/feature_generation.cpp)
hepta_runtime_target(hepta_feature_runtime)
target_link_libraries(hepta_feature_runtime PUBLIC
    hepta_marketdata_core
    hepta_numeric_core
    Threads::Threads
    OpenSSL::Crypto)

add_library(hepta_risk_core STATIC
    risk/deterministic_risk_policy.cpp)
hepta_runtime_target(hepta_risk_core)
target_link_libraries(hepta_risk_core PUBLIC hepta_observability_core)


add_library(hepta_oms_core STATIC
    oms_journal.cpp
    oms_recover.cpp)
hepta_runtime_target(hepta_oms_core)
target_link_libraries(hepta_oms_core PUBLIC
    hepta_observability_core
    Threads::Threads)

add_library(hepta_state_core STATIC
    state/authoritative_trading_snapshot_store.cpp
    state/snapshot_refresh_coordinator.cpp
    state/ib_contract_identity.cpp
    state/ib_authoritative_quote_subscription_set.cpp)
hepta_runtime_target(hepta_state_core)
target_link_libraries(hepta_state_core PUBLIC Threads::Threads)

add_library(hepta_intent_core STATIC
    intent/bounded_json.cpp
    intent/target_position_intent.cpp
    intent/authoritative_decision_snapshot.cpp)
hepta_runtime_target(hepta_intent_core)
target_link_libraries(hepta_intent_core PUBLIC OpenSSL::Crypto)

add_library(hepta_tool_protocol STATIC
    tool_host/typed_tool_framing.cpp
    tool_host/typed_tool_protocol.cpp
    tool_host/typed_tool_result_codec.cpp)
hepta_runtime_target(hepta_tool_protocol)
target_link_libraries(hepta_tool_protocol PUBLIC hepta_numeric_core)

add_library(hepta_execution_event_relay_core STATIC
    tool_host/execution_event_relay.cpp)
hepta_runtime_target(hepta_execution_event_relay_core)
target_link_libraries(hepta_execution_event_relay_core PUBLIC
    hepta_execution_contract
    hepta_agent_execution_support)

add_library(hepta_simulator_venue STATIC
    simulator/deterministic_execution_venue.cpp)
hepta_runtime_target(hepta_simulator_venue)

add_library(hepta_venue_ctp STATIC
    adapter_ctp/ctp_gateway_adapter.cpp)
hepta_runtime_target(hepta_venue_ctp)

add_library(hepta_venue_xt STATIC
    adapter_xt/xt_gateway_adapter.cpp)
hepta_runtime_target(hepta_venue_xt)

add_library(hepta_ib_adapter_core STATIC
    adapter_ib/ib_api_wrapper.cpp
    adapter_ib/ib_decimal_compat.cpp
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    adapter_ib/ib_order_lifecycle.cpp
    adapter_ib/ib_venue_correlation.cpp)
hepta_runtime_target(hepta_ib_adapter_core)
target_link_libraries(hepta_ib_adapter_core PUBLIC
    hepta_risk_core
    hepta_observability_core
    Threads::Threads)
add_library(hepta_execution_core STATIC
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
    execution/paper_terminal_mutation_manifest.cpp
    execution/execution_place_order_dispatch.cpp
    execution/execution_authoritative_flatten.cpp
    execution/execution_authoritative_flatten_dispatch.cpp
    execution/ib_paper_kill_switch.cpp)
hepta_runtime_target(hepta_execution_core)
target_link_libraries(hepta_execution_core PUBLIC
    hepta_execution_server
    hepta_execution_client
    hepta_agent_execution_support
    hepta_risk_core
    hepta_oms_core
    Threads::Threads
    OpenSSL::Crypto)

add_library(hepta_trading_tool_core STATIC
    tools/trading_tool_registry.cpp)
hepta_runtime_target(hepta_trading_tool_core)
target_link_libraries(hepta_trading_tool_core PUBLIC
    hepta_intent_core
    hepta_state_core
    OpenSSL::Crypto)

add_library(hepta_session_core STATIC
    tool_host/session_supervisor_lease_store.cpp
    tool_host/session_supervisor_protocol.cpp)
hepta_runtime_target(hepta_session_core)
target_link_libraries(hepta_session_core PUBLIC
    Threads::Threads
    OpenSSL::Crypto)

add_library(hepta_agent_os_core STATIC
    tool_host/tool_gateway_runtime_composition.cpp
    tool_host/tool_gateway_session_policy.cpp
    tool_host/agent_os_runtime_composition.cpp
    tool_host/agent_os_runtime_config.cpp
    tool_host/execution_gateway_runtime_composition.cpp
    tool_host/execution_gateway_runtime_config.cpp
    tool_host/session_supervisor_audit_journal.cpp
    tool_host/tool_decision_audit.cpp
    ${HEPTA_TOOL_HOST_CORE_SOURCES}
    tool_host/trading_tool_session_lifecycle.cpp
    tool_host/trading_tool_session_recovery.cpp
    tool_host/trading_tool_session_catalog.cpp
    tool_host/trading_tool_session_control_plane.cpp
    tool_host/trading_tool_watch_transaction.cpp
    tool_host/unix_session_supervisor_server.cpp
    tool_host/unix_tool_server.cpp)
hepta_runtime_target(hepta_agent_os_core)
target_link_libraries(hepta_agent_os_core PUBLIC
    hepta_observability_core
    hepta_tool_protocol
    hepta_execution_event_relay_core
    hepta_trading_tool_core
    hepta_execution_client
    hepta_agent_execution_support
    Threads::Threads
    OpenSSL::Crypto
    hepta_session_core)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_options(hepta_agent_os_core PRIVATE
        "$<$<CONFIG:Release>:-fno-rtti;-ffunction-sections;-fdata-sections>")
    foreach(target
            hepta_execution_contract
            hepta_execution_transport
            hepta_execution_client
            hepta_agent_execution_support
            hepta_trading_tool_core)
        target_compile_options(${target} PRIVATE
            "$<$<CONFIG:Release>:-ffunction-sections;-fdata-sections>")
    endforeach()
endif()

add_library(hepta_native_tool_client STATIC
    client/native_tool_discovery_contract.cpp
    client/native_tool_client.cpp
    tool_host/unix_tool_client.cpp)
hepta_runtime_target(hepta_native_tool_client)
target_link_libraries(hepta_native_tool_client PUBLIC
    hepta_tool_protocol)

add_executable(heptactl
    cli/heptactl.cpp
    cli/heptactl_command.cpp
    cli/heptactl_exit_codes.cpp)
hepta_runtime_target(heptactl)
target_link_libraries(heptactl PRIVATE hepta_native_tool_client)

add_executable(hepta_sessionctl
    cli/hepta_sessionctl.cpp
    cli/hepta_sessionctl_command.cpp
    cli/hepta_sessionctl_terminal_cleanup.cpp
    tool_host/unix_session_supervisor_client.cpp)
hepta_runtime_target(hepta_sessionctl)
set_target_properties(hepta_sessionctl PROPERTIES OUTPUT_NAME hepta-sessionctl)
target_link_libraries(hepta_sessionctl PRIVATE
    Threads::Threads
    OpenSSL::Crypto
    hepta_session_core)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(hepta_paper_terminal_latch_committer
        cli/hepta_paper_terminal_latch_committer.cpp
        execution/paper_terminal_external_latch.cpp)
    hepta_runtime_target(hepta_paper_terminal_latch_committer)
    set_target_properties(hepta_paper_terminal_latch_committer PROPERTIES
        OUTPUT_NAME hepta-paper-terminal-latch-committer)
    target_link_libraries(hepta_paper_terminal_latch_committer PRIVATE
        OpenSSL::Crypto)
endif()

add_executable(hepta_tool_gatewayd tool_host/hepta_tool_gatewayd.cpp)
hepta_runtime_target(hepta_tool_gatewayd)
set_target_properties(hepta_tool_gatewayd PROPERTIES
    OUTPUT_NAME hepta-tool-gatewayd
    SKIP_BUILD_RPATH TRUE)
target_link_libraries(hepta_tool_gatewayd PRIVATE hepta_agent_os_core)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_options(hepta_tool_gatewayd PRIVATE
        "$<$<CONFIG:Release>:-ffunction-sections;-fdata-sections;-fno-rtti>")
    target_link_options(hepta_tool_gatewayd PRIVATE
        $<$<CONFIG:Release>:-Wl,--gc-sections>
        $<$<CONFIG:Release>:-flto>)
    if(NOT CMAKE_NM OR NOT EXISTS "${CMAKE_NM}")
        message(FATAL_ERROR
            "Linux Gateway builds require CMAKE_NM for the privileged-symbol boundary")
    endif()
    add_custom_command(TARGET hepta_tool_gatewayd POST_BUILD
        COMMAND ${CMAKE_COMMAND}
            -DHEPTA_GATEWAY_BINARY=$<TARGET_FILE:hepta_tool_gatewayd>
            -DHEPTA_NM_EXECUTABLE=${CMAKE_NM}
            -DHEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET=$<IF:$<CONFIG:Release>,ON,OFF>
            -P ${CMAKE_CURRENT_LIST_DIR}/../cmake/verify_gateway_forbidden_symbols.cmake
        COMMENT "Verifying Gateway privileged-symbol boundary"
        VERBATIM)
endif()

add_executable(hepta_executiond
    execution/hepta_executiond.cpp
    execution/execution_service_runtime_config.cpp
    execution/execution_service_runtime_composition.cpp)
hepta_runtime_target(hepta_executiond)
set_target_properties(hepta_executiond PROPERTIES
    OUTPUT_NAME hepta-executiond
    SKIP_BUILD_RPATH TRUE)
target_link_libraries(hepta_executiond PRIVATE
    hepta_execution_core
    hepta_simulator_venue)

if(HEPTA_ENABLE_IBAPI)
    if(IBAPI_ROOT STREQUAL "")
        message(FATAL_ERROR
            "HEPTA_ENABLE_IBAPI=ON requires -DIBAPI_ROOT=<pinned CppClient source>")
    endif()
    get_filename_component(IBAPI_ROOT "${IBAPI_ROOT}" ABSOLUTE)
    set(HEPTA_IBAPI_CLIENT_SOURCE_NAMES
        ContractCondition.cpp
        Decimal.cpp
        DefaultEWrapper.cpp
        EClient.cpp
        EClientSocket.cpp
        EDecoder.cpp
        EMessage.cpp
        EMutex.cpp
        EOrderDecoder.cpp
        EReader.cpp
        EReaderOSSignal.cpp
        ESocket.cpp
        MarginCondition.cpp
        OperatorCondition.cpp
        OrderCondition.cpp
        PercentChangeCondition.cpp
        PriceCondition.cpp
        SoftDollarTier.cpp
        StdAfx.cpp
        TimeCondition.cpp
        Utils.cpp
        VolumeCondition.cpp
        executioncondition.cpp)
    set(HEPTA_IBAPI_CLIENT_SOURCES)
    foreach(source_name IN LISTS HEPTA_IBAPI_CLIENT_SOURCE_NAMES)
        set(source "${IBAPI_ROOT}/${source_name}")
        if(NOT EXISTS "${source}")
            message(FATAL_ERROR "IB C++ API source is missing: ${source}")
        endif()
        list(APPEND HEPTA_IBAPI_CLIENT_SOURCES "${source}")
    endforeach()
    foreach(header EClientSocket.h EWrapper.h Execution.h)
        if(NOT EXISTS "${IBAPI_ROOT}/${header}")
            message(FATAL_ERROR "IB C++ API header is missing: ${IBAPI_ROOT}/${header}")
        endif()
    endforeach()

    add_library(hepta_ibapi_client STATIC ${HEPTA_IBAPI_CLIENT_SOURCES})
    target_compile_features(hepta_ibapi_client PUBLIC cxx_std_17)
    target_include_directories(hepta_ibapi_client PUBLIC "${IBAPI_ROOT}")
    target_compile_definitions(hepta_ibapi_client PUBLIC HEPTA_ENABLE_IBAPI=1)

    target_compile_definitions(hepta_ib_adapter_core PUBLIC HEPTA_ENABLE_IBAPI=1)
    target_include_directories(hepta_ib_adapter_core PRIVATE "${IBAPI_ROOT}")
    target_link_libraries(hepta_ib_adapter_core PUBLIC hepta_ibapi_client)

    add_executable(hepta_ib_executiond
        execution/hepta_ib_executiond.cpp
        execution/ib_paper_execution_runtime_config.cpp
        execution/ib_paper_execution_runtime_composition.cpp
        execution/ib_paper_execution_runtime_state.cpp
        execution/ib_paper_execution_runtime_terminal_state.cpp
        execution/ib_paper_execution_runtime_events.cpp
        execution/ib_paper_execution_runtime_event_drain.cpp
        execution/ib_paper_execution_runtime_quote_admission.cpp
        execution/ib_paper_execution_runtime_recent_orders.cpp
        execution/ib_paper_execution_runtime_broker.cpp
        execution/ib_paper_execution_runtime_startup.cpp
        execution/ib_paper_execution_runtime_policy.cpp
        execution/ib_paper_execution_runtime_terminal.cpp
        execution/ib_paper_execution_runtime_flatten.cpp
        execution/ib_paper_execution_hook_authority.cpp
        execution/ib_paper_execution_profile.cpp
        execution/ib_paper_authoritative_flatten.cpp
        execution/ib_paper_execution_flatten_guard.cpp
        execution/ib_paper_flatten_plan_binding.cpp)
    hepta_runtime_target(hepta_ib_executiond)
    target_compile_definitions(hepta_ib_executiond PRIVATE HEPTA_ENABLE_IBAPI=1)
    target_include_directories(hepta_ib_executiond PRIVATE "${IBAPI_ROOT}")
    target_link_libraries(hepta_ib_executiond PRIVATE
        hepta_execution_core
        hepta_state_core
        hepta_ib_adapter_core
        hepta_ibapi_client)
    set_target_properties(hepta_ib_executiond PROPERTIES
        OUTPUT_NAME hepta-ib-executiond
        SKIP_BUILD_RPATH TRUE)

    if(WIN32)
        if(IBAPI_BIN STREQUAL "")
            set(IBAPI_BIN "${IBAPI_ROOT}")
        endif()
        file(GLOB IBAPI_DLLS "${IBAPI_BIN}/*.dll")
        if(IBAPI_DLLS)
            add_custom_command(TARGET hepta_ib_executiond POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    ${IBAPI_DLLS} $<TARGET_FILE_DIR:hepta_ib_executiond>)
        endif()
    endif()
endif()
