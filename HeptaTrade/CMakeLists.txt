cmake_minimum_required (VERSION 3.8)

#cmake_policy(SET CMP0060 NEW)

# set the project name
project (HeptaTrade)

find_package(OpenSSL REQUIRED)
include(GNUInstallDirs)

option(HEPTA_ENABLE_IBAPI "Enable Interactive Brokers API integration" OFF)
option(HEPTA_ENABLE_LEGACY_0DTE_BRIDGE
       "Allow the deprecated JSONL OpenClaw 0DTE bridge runtime path"
       OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue HeptaTrader composition binary"
       OFF)
set(IBAPI_ROOT "" CACHE PATH "Path to IB C++ API root (e.g. E:/trading/TWS API/source/cppclient)")
set(IBAPI_BIN "" CACHE PATH "Path to IB API runtime DLL directory (optional; if empty, IBAPI_ROOT is used)")

# Detect Ubuntu only for the optional legacy monolith library layout.
if(HEPTA_BUILD_LEGACY_MONOLITH)
    execute_process(
        COMMAND lsb_release -is
        OUTPUT_VARIABLE LINUX_DISTRO
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )
    if(LINUX_DISTRO STREQUAL "Ubuntu")
        set(IS_UBUNTU TRUE)
    else()
        set(IS_UBUNTU FALSE)
    endif()
endif()

include(CheckCXXCompilerFlag)
CHECK_CXX_COMPILER_FLAG("-std=c++11" COMPILER_SUPPORTS_CXX11)
CHECK_CXX_COMPILER_FLAG("-std=c++0x" COMPILER_SUPPORTS_CXX0X)

if(COMPILER_SUPPORTS_CXX11)
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++11")
elseif(COMPILER_SUPPORTS_CXX0X)
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++0x")
else()
    message(FATAL_ERROR
        "The compiler ${CMAKE_CXX_COMPILER} has no C++11 support")
endif()

set(CMAKE_CXX_FLAGS_DEBUG "$ENV{CXXFLAGS} -O0 -Wall -g -ggdb")
set(CMAKE_CXX_FLAGS_RELEASE "$ENV{CXXFLAGS} -O3 -Wall")

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_DEBUG
    "${CMAKE_BINARY_DIR}/bin/Debug")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE
    "${CMAKE_BINARY_DIR}/bin/Release")

add_definitions(-std=c++11)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_definitions(-D__LINUX__=1 -Dlinux)
endif()

include_directories ("../HeptaStrategy/")
include_directories ("../Interface/include/")
include_directories ("../Interface/")

if(HEPTA_ENABLE_IBAPI)
    if(IBAPI_ROOT STREQUAL "")
        message(FATAL_ERROR "HEPTA_ENABLE_IBAPI=ON requires -DIBAPI_ROOT=<IB C++ API root>")
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
        executioncondition.cpp
    )
    set(HEPTA_IBAPI_CLIENT_SOURCES)
    foreach(IBAPI_SOURCE_NAME IN LISTS HEPTA_IBAPI_CLIENT_SOURCE_NAMES)
        set(IBAPI_SOURCE "${IBAPI_ROOT}/${IBAPI_SOURCE_NAME}")
        if(NOT EXISTS "${IBAPI_SOURCE}")
            message(FATAL_ERROR
                "IB C++ API source is missing: ${IBAPI_SOURCE}. "
                "IBAPI_ROOT must point to the pinned CppClient source directory.")
        endif()
        list(APPEND HEPTA_IBAPI_CLIENT_SOURCES "${IBAPI_SOURCE}")
    endforeach()
    foreach(IBAPI_REQUIRED_HEADER EClientSocket.h EWrapper.h Execution.h)
        if(NOT EXISTS "${IBAPI_ROOT}/${IBAPI_REQUIRED_HEADER}")
            message(FATAL_ERROR
                "IB C++ API header is missing: ${IBAPI_ROOT}/${IBAPI_REQUIRED_HEADER}")
        endif()
    endforeach()

    add_library(hepta_ibapi_client STATIC ${HEPTA_IBAPI_CLIENT_SOURCES})
    target_include_directories(hepta_ibapi_client PUBLIC "${IBAPI_ROOT}")
    set_target_properties(hepta_ibapi_client PROPERTIES
        CXX_STANDARD 11
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)

    if(IBAPI_BIN STREQUAL "")
        if(EXISTS "${PROJECT_SOURCE_DIR}/../Interface/IBApi/bin")
            set(IBAPI_BIN "${PROJECT_SOURCE_DIR}/../Interface/IBApi/bin")
        else()
            set(IBAPI_BIN "${IBAPI_ROOT}")
        endif()
    endif()
    add_definitions(-DHEPTA_ENABLE_IBAPI)
    include_directories("${IBAPI_ROOT}")
    message(STATUS "HeptaTrade IB enabled. IBAPI_ROOT=${IBAPI_ROOT} IBAPI_BIN=${IBAPI_BIN}")
endif()

string(TOUPPER "${CMAKE_BUILD_TYPE}" HEPTA_BUILD_TYPE_UPPER)
if (HEPTA_BUILD_TYPE_UPPER STREQUAL "DEBUG")
    add_definitions(-g -DDEBUG)
    if(HEPTA_BUILD_LEGACY_MONOLITH)
        if(IS_UBUNTU)
            link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Ubuntu/Debug/")
        else()
            link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Linux/Debug/")
        endif()
        link_directories("${PROJECT_SOURCE_DIR}/../bin/Debug/")
    endif()
elseif (HEPTA_BUILD_TYPE_UPPER STREQUAL "RELEASE")
    add_definitions(-DNDEBUG -O3)
    if(HEPTA_BUILD_LEGACY_MONOLITH)
        if(IS_UBUNTU)
            link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Ubuntu/Release/")
        else()
            link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Linux/Release/")
        endif()
        link_directories("${PROJECT_SOURCE_DIR}/../bin/Release/")
    endif()
endif()

set(HEPTA_IB_GATEWAY_ADAPTER_SOURCES
    adapter_ib/ib_gateway_adapter.cpp
    adapter_ib/ib_gateway_adapter_event_state.cpp
    adapter_ib/ib_gateway_adapter_terminal.cpp
    adapter_ib/ib_gateway_adapter_order_submission.cpp
    adapter_ib/ib_gateway_adapter_reduce_only.cpp
)

set(HEPTA_EXECUTION_COORDINATOR_SOURCES
    execution/execution_coordinator.cpp
    execution/execution_coordinator_cancel.cpp
    execution/execution_coordinator_recovery.cpp
    execution/execution_coordinator_reconnect.cpp
    execution/execution_coordinator_terminal.cpp
)
set(HEPTA_TOOL_HOST_CORE_SOURCES
    tool_host/trading_tool_host.cpp
    tool_host/trading_tool_host_terminal.cpp
)

set(DIR_SRC
    HeptaDemoStrategyTrader.cpp
    ib_fx_multi_strategy.cpp
    oms_journal.cpp
    oms_recover.cpp
    openclaw_0dte_bridge.cpp
    order_watchdog.cpp
    adapter_ib/ib_api_wrapper.cpp
    adapter_ib/ib_decimal_compat.cpp
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    adapter_ib/ib_order_lifecycle.cpp
    adapter_ib/ib_venue_correlation.cpp
    adapter_ctp/ctp_gateway_adapter.cpp
    adapter_xt/xt_gateway_adapter.cpp
    risk/pre_trade_risk_engine.cpp
    reconcile/reconcile_engine.cpp
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
    execution/execution_place_order_dispatch.cpp
    execution/execution_authoritative_flatten.cpp
    execution/execution_authoritative_flatten_dispatch.cpp
    execution/execution_decision_lease_authority.cpp
    execution/ib_paper_kill_switch.cpp
    execution/ib_paper_execution_profile.cpp
    execution/ib_paper_authoritative_flatten.cpp
    execution/ib_paper_execution_flatten_guard.cpp
    execution/ib_paper_flatten_plan_binding.cpp
    execution/execution_event_feed.cpp
    execution/execution_event_feed_client.cpp
    execution/execution_event_feed_server.cpp
    execution/execution_event_feed_transport.cpp
    execution/execution_service_protocol.cpp
    execution/unix_execution_service.cpp
    execution/unix_execution_service_flatten.cpp
    execution/unix_execution_service_flatten_permit.cpp
    execution/unix_execution_service_client.cpp
    execution/unix_execution_service_flatten_client.cpp
    execution/unix_execution_service_transport.cpp
    tools/trading_tool_registry.cpp
    events/execution_event_hub.cpp
    events/owner_scoped_health_publisher.cpp
    tool_host/agent_os_runtime_composition.cpp
    tool_host/agent_os_runtime_config.cpp
    tool_host/execution_gateway_runtime_composition.cpp
    tool_host/execution_gateway_runtime_config.cpp
    tool_host/execution_event_relay.cpp
    tool_host/session_supervisor_audit_journal.cpp
    tool_host/session_supervisor_lease_store.cpp
    tool_host/session_supervisor_protocol.cpp
    tool_host/tool_decision_audit.cpp
    ${HEPTA_TOOL_HOST_CORE_SOURCES}
    tool_host/trading_tool_session_lifecycle.cpp
    tool_host/trading_tool_session_recovery.cpp
    tool_host/trading_tool_session_catalog.cpp
    tool_host/trading_tool_session_control_plane.cpp
    tool_host/trading_tool_watch_transaction.cpp
    tool_host/typed_tool_framing.cpp
    tool_host/typed_tool_protocol.cpp
    tool_host/typed_tool_result_codec.cpp
    tool_host/unix_session_supervisor_server.cpp
    tool_host/unix_tool_server.cpp
    simulator/deterministic_execution_venue.cpp
    agent/decision_lease_manager.cpp
    state/authoritative_trading_snapshot_store.cpp
    state/ib_authoritative_account_position_consumer.cpp
    state/ib_authoritative_open_order_consumer.cpp
    state/ib_authoritative_order_projector.cpp
    state/ib_authoritative_quote_subscription_set.cpp
    state/ib_authoritative_recovery_coordinator.cpp
    state/ib_authoritative_recovery_event_consumer.cpp
    state/ib_connection_lifecycle_state_machine.cpp
    state/ib_contract_identity.cpp
    state/snapshot_refresh_coordinator.cpp
)

add_library(CTPTradeLIB UNKNOWN IMPORTED)
set_property(TARGET CTPTradeLIB PROPERTY IMPORTED_LOCATION "${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/thosttraderapi_se.so")

add_library(CTPMdLIB UNKNOWN IMPORTED)
set_property(TARGET CTPMdLIB PROPERTY IMPORTED_LOCATION "${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/thostmduserapi_se.so")

if(HEPTA_BUILD_LEGACY_MONOLITH)
    set(HEPTA_CTP_OVERLAY_INPUTS
        "${PROJECT_SOURCE_DIR}/../third_party/ctp/6.7.7/include/ThostFtdcTraderApi.h")
    if(WIN32)
        if(CMAKE_SIZEOF_VOID_P EQUAL 8)
            set(HEPTA_CTP_PLATFORM_DIRECTORY "CTPTradeApi64")
        else()
            set(HEPTA_CTP_PLATFORM_DIRECTORY "CTPTradeApi32")
        endif()
        list(APPEND HEPTA_CTP_OVERLAY_INPUTS
            "${PROJECT_SOURCE_DIR}/../Interface/${HEPTA_CTP_PLATFORM_DIRECTORY}/thosttraderapi_se.lib"
            "${PROJECT_SOURCE_DIR}/../Interface/${HEPTA_CTP_PLATFORM_DIRECTORY}/thostmduserapi_se.lib")
    else()
        list(APPEND HEPTA_CTP_OVERLAY_INPUTS
            "${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/thosttraderapi_se.so"
            "${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/thostmduserapi_se.so")
    endif()
    foreach(HEPTA_CTP_OVERLAY_INPUT IN LISTS HEPTA_CTP_OVERLAY_INPUTS)
        if(NOT EXISTS "${HEPTA_CTP_OVERLAY_INPUT}")
            message(FATAL_ERROR
                "The legacy monolith requires the separately reviewed, "
                "nonredistributable CTP 6.7.7 overlay; missing "
                "${HEPTA_CTP_OVERLAY_INPUT}")
        endif()
    endforeach()
endif()

add_library(hepta_execution_contract STATIC
    execution/execution_service_protocol.cpp
    execution/execution_event_feed.cpp
)
target_include_directories(hepta_execution_contract
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_library(hepta_execution_transport STATIC
    execution/execution_event_feed_transport.cpp
    execution/unix_execution_service_transport.cpp
)
target_include_directories(hepta_execution_transport
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_library(hepta_execution_client STATIC
    execution/execution_event_feed_client.cpp
    execution/unix_execution_service_client.cpp
    execution/unix_execution_service_flatten_client.cpp
)
target_include_directories(hepta_execution_client
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_execution_client
    PUBLIC hepta_execution_contract hepta_execution_transport pthread)

add_library(hepta_execution_server STATIC
    execution/execution_decision_lease_authority.cpp
    execution/execution_event_feed_server.cpp
    execution/unix_execution_service.cpp
    execution/unix_execution_service_flatten.cpp
    execution/unix_execution_service_flatten_permit.cpp
)
target_include_directories(hepta_execution_server
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_execution_server
    PUBLIC hepta_execution_contract hepta_execution_transport pthread)

add_library(hepta_agent_execution_support STATIC
    agent/decision_lease_manager.cpp
    events/execution_event_hub.cpp
)
target_include_directories(hepta_agent_execution_support
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_agent_execution_support PUBLIC pthread)

add_library(hepta_execution_core STATIC
    ${HEPTA_EXECUTION_COORDINATOR_SOURCES}
    execution/paper_terminal_mutation_manifest.cpp
    execution/execution_place_order_dispatch.cpp
    execution/execution_authoritative_flatten.cpp
    execution/execution_authoritative_flatten_dispatch.cpp
    oms_journal.cpp
)
target_include_directories(hepta_execution_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_execution_core PUBLIC
    hepta_execution_server
    hepta_execution_client
    hepta_agent_execution_support
    pthread
    OpenSSL::Crypto)

add_library(hepta_trading_tool_core STATIC
    state/ib_contract_identity.cpp
    tools/trading_tool_registry.cpp
)
target_include_directories(hepta_trading_tool_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_trading_tool_core PUBLIC OpenSSL::Crypto)

add_library(hepta_agent_os_core STATIC
    tool_host/tool_gateway_runtime_composition.cpp
    tool_host/tool_gateway_session_policy.cpp
    tool_host/agent_os_runtime_composition.cpp
    tool_host/agent_os_runtime_config.cpp
    tool_host/execution_gateway_runtime_composition.cpp
    tool_host/execution_gateway_runtime_config.cpp
    tool_host/execution_event_relay.cpp
    tool_host/session_supervisor_audit_journal.cpp
    tool_host/session_supervisor_lease_store.cpp
    tool_host/session_supervisor_protocol.cpp
    tool_host/tool_decision_audit.cpp
    ${HEPTA_TOOL_HOST_CORE_SOURCES}
    tool_host/trading_tool_session_lifecycle.cpp
    tool_host/trading_tool_session_recovery.cpp
    tool_host/trading_tool_session_catalog.cpp
    tool_host/trading_tool_session_control_plane.cpp
    tool_host/trading_tool_watch_transaction.cpp
    tool_host/typed_tool_framing.cpp
    tool_host/typed_tool_protocol.cpp
    tool_host/typed_tool_result_codec.cpp
    tool_host/unix_session_supervisor_server.cpp
    tool_host/unix_tool_server.cpp
)
target_include_directories(hepta_agent_os_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_agent_os_core PUBLIC
    hepta_trading_tool_core
    hepta_execution_client
    hepta_agent_execution_support
    pthread
    OpenSSL::Crypto)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_options(hepta_agent_os_core PRIVATE
        $<$<CONFIG:Release>:-fno-rtti>)
    set(HEPTA_GATEWAY_RELEASE_SECTION_TARGETS
        hepta_execution_contract
        hepta_execution_transport
        hepta_execution_client
        hepta_agent_execution_support
        hepta_trading_tool_core
        hepta_agent_os_core)
    foreach(HEPTA_GATEWAY_RELEASE_SECTION_TARGET
            IN LISTS HEPTA_GATEWAY_RELEASE_SECTION_TARGETS)
        target_compile_options(
            ${HEPTA_GATEWAY_RELEASE_SECTION_TARGET} PRIVATE
            $<$<CONFIG:Release>:-ffunction-sections>
            $<$<CONFIG:Release>:-fdata-sections>)
    endforeach()
    if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        foreach(HEPTA_GATEWAY_TRANSPORT_TARGET
                hepta_execution_client
                hepta_agent_os_core)
            target_compile_options(${HEPTA_GATEWAY_TRANSPORT_TARGET} PRIVATE
                $<$<CONFIG:Release>:-fno-reorder-blocks-and-partition>)
        endforeach()
    endif()
endif()

add_library(hepta_native_tool_client STATIC
    client/native_tool_discovery_contract.cpp
    client/native_tool_client.cpp
    tool_host/unix_tool_client.cpp
    tool_host/typed_tool_framing.cpp
    tool_host/typed_tool_protocol.cpp
    tool_host/typed_tool_result_codec.cpp
)
target_include_directories(hepta_native_tool_client PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_compile_features(hepta_native_tool_client PUBLIC cxx_std_11)
set_target_properties(hepta_native_tool_client PROPERTIES
    EXPORT_NAME NativeToolClient)

if(HEPTA_BUILD_LEGACY_MONOLITH)
    add_executable(HeptaTrader ${DIR_SRC})
endif()

add_executable(heptactl
    cli/heptactl.cpp
    cli/heptactl_command.cpp
    cli/heptactl_exit_codes.cpp
)
target_include_directories(heptactl PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/adapter_ib
)
target_link_libraries(heptactl hepta_native_tool_client)

add_executable(hepta_sessionctl
    cli/hepta_sessionctl.cpp
    cli/hepta_sessionctl_command.cpp
    cli/hepta_sessionctl_terminal_cleanup.cpp
    tool_host/session_supervisor_lease_store.cpp
    tool_host/unix_session_supervisor_client.cpp
    tool_host/session_supervisor_protocol.cpp
)
set_target_properties(hepta_sessionctl PROPERTIES OUTPUT_NAME "hepta-sessionctl")
target_include_directories(hepta_sessionctl PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(hepta_sessionctl pthread OpenSSL::Crypto)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(hepta_paper_terminal_latch_committer
        cli/hepta_paper_terminal_latch_committer.cpp
        execution/paper_terminal_external_latch.cpp
    )
    set_target_properties(hepta_paper_terminal_latch_committer PROPERTIES
        OUTPUT_NAME "hepta-paper-terminal-latch-committer")
    target_include_directories(hepta_paper_terminal_latch_committer PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_features(hepta_paper_terminal_latch_committer PRIVATE
        cxx_std_11)
    target_link_libraries(hepta_paper_terminal_latch_committer
        OpenSSL::Crypto)
endif()

add_executable(hepta_tool_gatewayd
    tool_host/hepta_tool_gatewayd.cpp
)
set_target_properties(hepta_tool_gatewayd PROPERTIES
    OUTPUT_NAME "hepta-tool-gatewayd"
    SKIP_BUILD_RPATH TRUE)
target_include_directories(hepta_tool_gatewayd PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/adapter_ib)
target_link_libraries(hepta_tool_gatewayd hepta_agent_os_core)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_options(hepta_tool_gatewayd PRIVATE
        $<$<CONFIG:Release>:-ffunction-sections>
        $<$<CONFIG:Release>:-fdata-sections>
        $<$<CONFIG:Release>:-fno-rtti>)
    set_property(TARGET hepta_tool_gatewayd APPEND_STRING
        PROPERTY LINK_FLAGS_RELEASE " -Wl,--gc-sections")
    if(NOT CMAKE_NM OR NOT EXISTS "${CMAKE_NM}")
        message(FATAL_ERROR
            "Linux Agent-OS builds require a valid CMAKE_NM for the "
            "Gateway privileged-symbol boundary")
    endif()
    add_custom_command(TARGET hepta_tool_gatewayd POST_BUILD
        COMMAND ${CMAKE_COMMAND}
            -DHEPTA_GATEWAY_BINARY=$<TARGET_FILE:hepta_tool_gatewayd>
            -DHEPTA_NM_EXECUTABLE=${CMAKE_NM}
            -DHEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET=$<IF:$<CONFIG:Release>,ON,OFF>
            -P ${CMAKE_CURRENT_LIST_DIR}/../cmake/verify_gateway_forbidden_symbols.cmake
        COMMENT "Verifying Agent Gateway privileged-symbol boundary"
        VERBATIM)
endif()

add_executable(hepta_executiond
    execution/hepta_executiond.cpp
    execution/execution_service_runtime_config.cpp
    execution/execution_service_runtime_composition.cpp
    simulator/deterministic_execution_venue.cpp
)
set_target_properties(hepta_executiond PROPERTIES
    OUTPUT_NAME "hepta-executiond"
    SKIP_BUILD_RPATH TRUE)
target_include_directories(hepta_executiond PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/adapter_ib
)
target_link_libraries(hepta_executiond hepta_execution_core)

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
    execution/ib_paper_kill_switch.cpp
    execution/ib_paper_execution_profile.cpp
    execution/ib_paper_authoritative_flatten.cpp
    execution/ib_paper_execution_flatten_guard.cpp
    execution/ib_paper_flatten_plan_binding.cpp
    adapter_ib/ib_api_wrapper.cpp
    adapter_ib/ib_decimal_compat.cpp
    ${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}
    adapter_ib/ib_order_lifecycle.cpp
    adapter_ib/ib_venue_correlation.cpp
    risk/pre_trade_risk_engine.cpp
    state/authoritative_trading_snapshot_store.cpp
    state/ib_authoritative_quote_subscription_set.cpp
    state/ib_contract_identity.cpp
)
target_include_directories(hepta_ib_executiond PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/adapter_ib)
target_link_libraries(hepta_ib_executiond hepta_execution_core)
if(HEPTA_ENABLE_IBAPI)
    target_link_libraries(hepta_ib_executiond hepta_ibapi_client)
endif()
set_target_properties(hepta_ib_executiond PROPERTIES SKIP_BUILD_RPATH TRUE)
if(HEPTA_ENABLE_IBAPI)
    set_target_properties(hepta_ib_executiond PROPERTIES
        OUTPUT_NAME hepta-ib-executiond)
else()
    set_target_properties(hepta_ib_executiond PROPERTIES
        OUTPUT_NAME hepta-ib-executiond-disabled)
endif()

# Distribution, install-tree, source-bundle and certification declarations
# were intentionally removed. This file now defines runtime build targets only.

if(HEPTA_ENABLE_LEGACY_0DTE_BRIDGE AND NOT HEPTA_BUILD_LEGACY_MONOLITH)
    message(FATAL_ERROR
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE requires "
        "HEPTA_BUILD_LEGACY_MONOLITH=ON")
endif()

if(HEPTA_BUILD_LEGACY_MONOLITH)
    if(HEPTA_ENABLE_LEGACY_0DTE_BRIDGE)
        target_compile_definitions(
            HeptaTrader PRIVATE HEPTA_ENABLE_LEGACY_0DTE_BRIDGE=1)
        message(WARNING
            "Deprecated OpenClaw 0DTE JSONL bridge runtime path is enabled")
    endif()
    target_link_libraries(HeptaTrader pthread OpenSSL::Crypto)
    target_link_libraries(
        HeptaTrader HeptaStrategy libheptaHeptaDLL_Linux.a libTinyXml_Linux.a)
    target_link_libraries(HeptaTrader CTPTradeLIB CTPMdLIB)
    if(HEPTA_ENABLE_IBAPI)
        target_link_libraries(HeptaTrader hepta_ibapi_client)
    endif()
endif()

if(HEPTA_ENABLE_IBAPI AND WIN32)
    file(GLOB IBAPI_DLLS "${IBAPI_BIN}/*.dll")
    if(IBAPI_DLLS)
        set(IBAPI_RUNTIME_TARGETS hepta_ib_executiond)
        if(HEPTA_BUILD_LEGACY_MONOLITH)
            list(APPEND IBAPI_RUNTIME_TARGETS HeptaTrader)
        endif()
        foreach(IBAPI_RUNTIME_TARGET IN LISTS IBAPI_RUNTIME_TARGETS)
            add_custom_command(TARGET ${IBAPI_RUNTIME_TARGET} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E echo "[IB] Copy runtime DLLs from ${IBAPI_BIN}"
                COMMAND ${CMAKE_COMMAND} -E copy_if_different ${IBAPI_DLLS} $<TARGET_FILE_DIR:${IBAPI_RUNTIME_TARGET}>
            )
        endforeach()
    else()
        message(WARNING "HEPTA_ENABLE_IBAPI=ON but no DLL found in IBAPI_BIN=${IBAPI_BIN}")
    endif()
endif()
