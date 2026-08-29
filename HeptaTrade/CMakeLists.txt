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

# ����Ƿ�Ϊ Ubuntu ϵͳ
execute_process(
    COMMAND lsb_release -is
    OUTPUT_VARIABLE LINUX_DISTRO
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# ����ϵͳ��ر���
if(LINUX_DISTRO STREQUAL "Ubuntu")
    set(IS_UBUNTU TRUE)
    message(STATUS "Detected Ubuntu system")
else()
    set(IS_UBUNTU FALSE)
    message(STATUS "Detected non-Ubuntu system: ${LINUX_DISTRO}")
endif()


# ���c++��������־������c++11֧�ֱ���
include(CheckCXXCompilerFlag)
CHECK_CXX_COMPILER_FLAG("-std=c++11" COMPILER_SUPPORTS_CXX11)
CHECK_CXX_COMPILER_FLAG("-std=c++0x" COMPILER_SUPPORTS_CXX0X)

if(COMPILER_SUPPORTS_CXX11)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++11")
elseif(COMPILER_SUPPORTS_CXX0X)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++0x")
else()
message(STATUS "The compiler ${CMAKE_CXX_COMPILER} has no C++11 support. Please use a different C++ compiler.")
endif()

#set(CMAKE_EXE_LINKER_FLAGS "-no-pie")

set(CMAKE_CXX_FLAGS_DEBUG "$ENV{CXXFLAGS} -O0 -Wall -g -ggdb")  # Debugģʽ�µı���ָ��
set(CMAKE_CXX_FLAGS_RELEASE "$ENV{CXXFLAGS} -O3 -Wall")         # Releaseģʽ�µı���ָ��

# Keep every configure/build tree self-contained. Writing executables into the
# source tree lets an IB-disabled build silently overwrite an IB-enabled one.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_DEBUG
    "${CMAKE_BINARY_DIR}/bin/Debug")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE
    "${CMAKE_BINARY_DIR}/bin/Release")

add_definitions(-std=c++11)
add_definitions(-D__LINUX__=1 -Dlinux)


include_directories ("../HeptaStrategy/")
include_directories ("../Interface/include/")
include_directories ("../Interface/")

if(HEPTA_ENABLE_IBAPI)
    if(IBAPI_ROOT STREQUAL "")
        message(FATAL_ERROR "HEPTA_ENABLE_IBAPI=ON requires -DIBAPI_ROOT=<IB C++ API root>")
    endif()
    get_filename_component(IBAPI_ROOT "${IBAPI_ROOT}" ABSOLUTE)

    # This is the reviewed C++ client compilation closure. Do not glob the SDK
    # directory: an SDK upgrade must make source additions/removals explicit.
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

#link_directories("${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/")
#link_directories("${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux")

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

# Keep the coordinator and Agent-OS terminal extensions in one reviewed
# source list.  The legacy monolith and the split core both consume the same
# exact files; centralizing the list prevents the two closures from drifting.
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

# Keep the production compilation closure explicit. This prevents experimental
# or generated sources from becoming part of HeptaTrader merely by appearing
# in one of these directories.
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

# add the executable
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

# Keep the Release Gateway's linked code surface bounded without weakening the
# post-link symbol verifier. Every static-library target in the Gateway
# closure must emit independently collectable sections so the linker can
# discard functions and data that are not reachable from the Agent-facing
# process. Keep this list exact; adding a Gateway dependency requires an
# explicit review here.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    # The Agent-facing core performs no runtime type inspection.  Do not emit
    # unused RTTI metadata into the Gateway TCB; polymorphic dispatch remains
    # available through the reviewed virtual interfaces.
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
    # GCC otherwise emits separately named hot/cold block partitions in the
    # two Agent-facing transport targets. They do not add an API, but do
    # consume the strict post-link defined-symbol budget. Keep normal
    # optimization and section GC while using one symbol per reviewed
    # function. Applying the option at target scope also avoids duplicating
    # source-path references outside the explicit production closures above.
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

# Agent-facing OS process. It owns no broker adapter, credentials, OMS,
# strategy, risk engine, or local execution authority. Mutations can only cross
# the authenticated remote Execution Service boundary.
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
        COMMENT "Verifying Agent Gateway privileged-symbol boundary and Release budget"
        VERBATIM)
endif()

# Simulator-only privileged process boundary. This target deliberately links no
# broker adapter, CTP/XT library, strategy, Tool Gateway, or Agent-facing code.
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

# Separate broker-owning PAPER process. It is default-hard-off and shares no
# executable or runtime mode with the Simulator authority.
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
    # Keep the compile-only stub visibly distinct even inside a build tree.
    set_target_properties(hepta_ib_executiond PROPERTIES
        OUTPUT_NAME hepta-ib-executiond-disabled)
endif()
set(HEPTA_EXECUTION_INSTALL_COMPONENT "hepta-execution-runtime")
set(HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR "lib/systemd/system")
set(HEPTA_EXECUTION_TMPFILES_INSTALL_DIR "lib/tmpfiles.d")
set(HEPTA_EXECUTION_DOC_INSTALL_DIR
    "${CMAKE_INSTALL_DATAROOTDIR}/doc/heptatrader")
set(HEPTA_AGENT_OS_INSTALL_COMPONENT "hepta-agent-os-runtime")
set(HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT "hepta-agent-os-sdk")
set(HEPTA_AGENT_OS_SDK_CMAKE_INSTALL_DIR
    "${CMAKE_INSTALL_LIBDIR}/cmake/HeptaTraderAgentOsSdk")

# The reviewed units intentionally use FHS paths under /usr. Refuse a silent
# /usr/local install whose service files would point at a different libexec
# tree and whose units would not be discovered by the system manager.
install(CODE "
    if(NOT \"\${CMAKE_INSTALL_PREFIX}\" STREQUAL \"/usr\")
        message(FATAL_ERROR
            \"hepta-execution-runtime requires --prefix /usr\")
    endif()
" COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")

install(CODE "
    if(NOT \"\${CMAKE_INSTALL_PREFIX}\" STREQUAL \"/usr\")
        message(FATAL_ERROR
            \"hepta-agent-os-runtime requires --prefix /usr\")
    endif()
" COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")

install(TARGETS hepta_tool_gatewayd
    RUNTIME DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    PERMISSIONS
        OWNER_READ OWNER_WRITE OWNER_EXECUTE
        GROUP_READ GROUP_EXECUTE
        WORLD_READ WORLD_EXECUTE
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(TARGETS heptactl hepta_sessionctl
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    PERMISSIONS
        OWNER_READ OWNER_WRITE OWNER_EXECUTE
        GROUP_READ GROUP_EXECUTE
        WORLD_READ WORLD_EXECUTE
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_campaignctl.py"
    DESTINATION "${CMAKE_INSTALL_BINDIR}"
    RENAME hepta-campaignctl
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_paper_receipt_contracts.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-paper-receipt-contracts
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_paper_receipt_contracts_v2_compat.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-paper-receipt-contracts-v2-compat
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_executor.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-executor
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_backend_adapter.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-backend-adapter
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_crash_emergency_closer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-crash-emergency-closer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_handoff_producer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-handoff-producer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_launch_joiner.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-launch-joiner
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_owner_provisioner.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-owner-provisioner
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_root_coordinator.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-root-coordinator
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_terminal_prover.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-canary-terminal-prover
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(TARGETS hepta_native_tool_client
    EXPORT HeptaTraderAgentOsSdkTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
        COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
include(CMakePackageConfigHelpers)
configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/HeptaTraderAgentOsSdkConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/HeptaTraderAgentOsSdkConfig.cmake"
    INSTALL_DESTINATION "${HEPTA_AGENT_OS_SDK_CMAKE_INSTALL_DIR}"
    NO_SET_AND_CHECK_MACRO
    NO_CHECK_REQUIRED_COMPONENTS_MACRO)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/HeptaTraderAgentOsSdkConfigVersion.cmake"
    VERSION "0.1.0"
    COMPATIBILITY SameMajorVersion)
install(EXPORT HeptaTraderAgentOsSdkTargets
    FILE HeptaTraderAgentOsSdkTargets.cmake
    NAMESPACE HeptaTrader::
    DESTINATION "${HEPTA_AGENT_OS_SDK_CMAKE_INSTALL_DIR}"
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/HeptaTraderAgentOsSdkConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/HeptaTraderAgentOsSdkConfigVersion.cmake"
    DESTINATION "${HEPTA_AGENT_OS_SDK_CMAKE_INSTALL_DIR}"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/client/native_tool_client.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/client/native_tool_discovery_contract.h"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/heptatrader/client"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/tool_host/typed_tool_protocol.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/tool_host/trading_tool_host.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/tool_host/trading_tool_session_catalog.h"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/heptatrader/tool_host"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/tools/trading_tool_registry.h"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/heptatrader/tools"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/execution/execution_authority.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/execution/trading_contract.h"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/heptatrader/execution"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/agent/decision_lease_manager.h"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/heptatrader/agent"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_SDK_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../adapters/mcp/hepta_mcp_server.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-mcp-server
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_agent_session_bootstrap.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-agent-session-bootstrap
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_shadow_watch_custodian.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-shadow-watch-custodian
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_agent_mcp_launcher.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-agent-mcp-launcher
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_agent_trust_domain.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_shadow_watch_collector.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-shadow-watch-collector
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_shadow_watch_exporter.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-shadow-watch-exporter
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_shadow_host_installer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-shadow-host-installer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_watch_profile_deployer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-watch-profile-deployer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_watch_activation_transaction.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-watch-activation-transaction
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_shadow_host_controller.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-shadow-host-controller
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_load_probe_validator.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-load-probe-validator
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_hepta_p1_observation_policy.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME build-hepta-p1-observation-policy
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_shadow_observer_controller.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-shadow-observer-controller
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_shadow_admission_launcher.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-shadow-admission-launcher
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_campaign_freezer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-campaign-freezer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_policy_planner.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-policy-planner
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_campaign_coordinator.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-campaign-coordinator
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_observer_worker.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-observer-worker
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_recorder_worker.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-recorder-worker
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_fault_pin_producer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-fault-pin-producer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_evidence_recorder.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-evidence-recorder
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_independent_observer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-independent-observer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_root_fault_injector.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-root-fault-injector
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_safety_soak_auditor.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-safety-soak-auditor
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_watch_to_paper_handoff.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-watch-to-paper-handoff
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_kill_switch_bootstrap.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-kill-switch-bootstrap
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_admission_verifier.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-admission-verifier
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-zero-exposure-snapshot-producer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_zero_exposure_attestor.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-zero-exposure-attestor
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_terminal_witness_verifier.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-p1-paper-terminal-witness-verifier
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_rootful_review_closure_consumer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-rootful-review-closure-consumer
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_rootful_systemd_environment_provenance.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-rootful-systemd-environment-provenance
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_rootful_review_closure_consumer.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_release_validation_closure.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-release-validation-closure-verifier
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/aggregate_hepta_execution_native_systemd_gate.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_hepta_execution_native_vm_bundle.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_heptatrader_delivery_closure.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_heptatrader_engineering_closure.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_heptatrader_evidence_index.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_heptatrader_evidence_ingestion_request.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_heptatrader_release_validation_closure.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_heptatrader_verification_evidence.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_hepta_agent_os_provisioned_host.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/converge_ctp_vendor_headers.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_service_identities.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/heptatrader_secure_artifacts.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_execution_gateway_soak.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_execution_native_systemd_gate.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_hepta_execution_rootful_systemd_gate.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_hepta_execution_native_vm_bundle.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_agent_os_source_bundle.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_clean_source_bundle.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_delivery_closure.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_engineering_closure.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_evidence_index.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_evidence_ingestion_receipt.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_evidence_set.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_prebuilt_assets.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_release_validation_closure.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_runtime_package.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/verify_heptatrader_vendor_assets.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../hepta_ops/__init__.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../hepta_ops/agent_os_source.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../hepta_ops/registry.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/hepta_ops"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_bounded_shadow_closure_verifier.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-bounded-shadow-closure-verifier
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_official_source_capture.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-official-source-capture
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_bounded_shadow_observer.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_market_context_builder.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_market_evidence_normalizer.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_market_official_source_extractor.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_eurusd_confirmed_momentum_strategy.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_shadow_market_history.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_strategy_replay_evaluator.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_strategy_shadow_runner.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/validate_hepta_strategy_decision_receipt.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_strategy_contracts.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_broker_egress_policy.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-broker-egress-policy
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_local_paper_control.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME hepta-local-paper-control
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(PROGRAMS
    "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/check_hepta_agent_os_provisioned_host.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    RENAME check-hepta-agent-os-provisioned-host
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-service-identities-v1.json"
    "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-broker-network-policy-v1.json"
    DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../.agents/plugins/marketplace.json"
    DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader/.agents/plugins"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../plugins/heptatrader-agent-os/.mcp.json"
    "${CMAKE_CURRENT_SOURCE_DIR}/../plugins/heptatrader-agent-os/README.md"
    DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader/plugins/heptatrader-agent-os"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../plugins/heptatrader-agent-os/.codex-plugin/plugin.json"
    DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader/plugins/heptatrader-agent-os/.codex-plugin"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-session-supervisor.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway@.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-session-supervisor@.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-broker-egress-policy.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-watch-activation.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-watch-activation-reconcile.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-watch-activation-reconcile.timer"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-canary-capture.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-canary-executor.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-canary-root-coordinator.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-fail-close@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-terminal-cutoff@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-terminal-witness-verifier@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-safety-soak-campaign@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-safety-soak-observer-worker@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-safety-soak-recorder-worker@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-safety-soak@.target"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-collector@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-export@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-collector@.timer"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-custodian@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-custodian-reconcile@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-custodian-reconcile@.timer"
        DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-systemd-gate.apparmor"
        DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader/systemd"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway.env.example"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway-domain.env.example"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-agent-trust-domain.json.example"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-agent-trust-domain-policy-v1.json"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-agent-host-identity.conf.example"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-agent-broker-egress-policy.conf.example"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-agent-trust-domain-paper-identities-v1.json.example"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-shadow-watch-domain.env.example"
        DESTINATION "${HEPTA_EXECUTION_DOC_INSTALL_DIR}/examples"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../tmpfiles.d/heptatrader-agent-os.conf"
        DESTINATION "${HEPTA_EXECUTION_TMPFILES_INSTALL_DIR}"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
        "${CMAKE_CURRENT_SOURCE_DIR}/../docs/BROKER-NETWORK-ISOLATION.md"
        "${CMAKE_CURRENT_SOURCE_DIR}/../docs/EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md"
        "${CMAKE_CURRENT_SOURCE_DIR}/../docs/RUNBOOK-STARTUP.md"
        DESTINATION "${HEPTA_EXECUTION_DOC_INSTALL_DIR}"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../strategies/eurusd-confirmed-momentum-shadow-v2.json"
        DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader/strategies"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway.service.d/10-hepta-broker-egress-policy.conf"
        DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}/hepta-tool-gateway.service.d"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-tool-gateway@.service.d/10-hepta-broker-egress-policy.conf"
        DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}/hepta-tool-gateway@.service.d"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_AGENT_OS_INSTALL_COMPONENT}")
endif()

install(TARGETS hepta_executiond
    RUNTIME DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
    PERMISSIONS
        OWNER_READ OWNER_WRITE OWNER_EXECUTE
        GROUP_READ GROUP_EXECUTE
        WORLD_READ WORLD_EXECUTE
    COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-service-identities-v1.json"
    DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader"
    PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
    COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")

# This component is a passive deployment bundle. It intentionally installs no
# /etc configuration, credentials, runtime directories, users, or enabled
# units. Installed tmpfiles declarations take effect only when the host
# explicitly processes them; they never create a token or trading authority.
# Provisioning those host-owned resources remains a separate gate.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-simulator.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-simulator.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-events-simulator.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-simulator@.service"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-simulator@.socket"
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-events-simulator@.socket"
        DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-simulator.env.example"
        DESTINATION "${HEPTA_EXECUTION_DOC_INSTALL_DIR}/examples"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
    install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/../docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
        DESTINATION "${HEPTA_EXECUTION_DOC_INSTALL_DIR}"
        PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
        COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
endif()

if(HEPTA_ENABLE_IBAPI)
    # Never package the deliberately disconnected IB-disabled stub as a broker
    # authority. The real target is installable only from an IB-enabled build.
    install(TARGETS hepta_ib_executiond
        RUNTIME DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
        PERMISSIONS
            OWNER_READ OWNER_WRITE OWNER_EXECUTE
            GROUP_READ GROUP_EXECUTE
            WORLD_READ WORLD_EXECUTE
        COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        install(TARGETS hepta_paper_terminal_latch_committer
            RUNTIME DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            PERMISSIONS
                OWNER_READ OWNER_WRITE OWNER_EXECUTE
                GROUP_READ GROUP_EXECUTE
                WORLD_READ WORLD_EXECUTE
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-paper-terminal-latch-committer@.service"
            DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_broker_egress_policy.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-broker-egress-policy
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_ib_paper_domain_authority.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-ib-paper-domain-authority
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_ib_paper_campaign_operator.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-ib-paper-campaign-operator
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_local_paper_control.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-paper-control
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_canary_root_finalizer.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-p1-paper-canary-finalizer
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_p1_paper_terminal_witness_verifier.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-p1-paper-terminal-witness-verifier
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/hepta_local_ai_paper_agent.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-ai-paper-agent
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_paper_safe_recover.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-paper-safe-recover
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_paper_repair.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-paper-repair
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_paper_safe_recover_guard.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-paper-safe-recover-guard
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_paper_session_renew.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-paper-session-renew
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_paper_supervisor.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-local-paper-supervisor
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(PROGRAMS
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/prepare_repair_campaign.py"
            DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}"
            RENAME hepta-prepare-paper-campaign
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-broker-network-policy-v1.json"
            DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper.socket"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-events-ib-paper.socket"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper@.socket"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-events-ib-paper@.socket"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-broker-egress-policy.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-authority@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-fail-close@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-canary-finalizer.socket"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-canary-finalizer@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-terminal-cutoff@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-p1-paper-terminal-witness-verifier@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-domain-preflight@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-campaign-operator@.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-campaign-operator@.socket"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-ai-paper-agent.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-safe-recover.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-safe-recover.timer"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-session-renew.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-session-renew.timer"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-supervisor.service"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-paper-supervisor.timer"
            DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../configs/hepta-local-ai-paper-strategy-v1.json"
            "${CMAKE_CURRENT_SOURCE_DIR}/../configs/hepta-local-ai-paper-strategy-v2.json"
            "${CMAKE_CURRENT_SOURCE_DIR}/../configs/hepta-local-ai-paper-strategy-v3.json"
            DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/heptatrader"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper.env.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-gateway-paper.env.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper-domain.env.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-gateway-paper-domain.env.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-local-ai-paper-agent.env.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-agent-trust-domain-paper-identities-v1.json.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-domain-authorizations-v1.json.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-campaign-policy-v1.json.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-campaign-policy-local-v4.json.example"
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-ib-paper-campaign-policy-p1-v5.json.example"
            DESTINATION "${HEPTA_EXECUTION_DOC_INSTALL_DIR}/examples"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper.service.d/10-hepta-broker-egress-policy.conf"
            DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}/hepta-execution-ib-paper.service.d"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../systemd/hepta-execution-ib-paper@.service.d/10-hepta-broker-egress-policy.conf"
            DESTINATION "${HEPTA_EXECUTION_SYSTEMD_INSTALL_DIR}/hepta-execution-ib-paper@.service.d"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../docs/BROKER-NETWORK-ISOLATION.md"
            "${CMAKE_CURRENT_SOURCE_DIR}/../docs/AUTONOMOUS-PAPER-CAMPAIGN.md"
            "${CMAKE_CURRENT_SOURCE_DIR}/../docs/RUNBOOK-STARTUP.md"
            DESTINATION "${HEPTA_EXECUTION_DOC_INSTALL_DIR}"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
        install(FILES
            "${CMAKE_CURRENT_SOURCE_DIR}/../tmpfiles.d/heptatrader-ib-paper.conf"
            DESTINATION "${HEPTA_EXECUTION_TMPFILES_INSTALL_DIR}"
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ
            COMPONENT "${HEPTA_EXECUTION_INSTALL_COMPONENT}")
    endif()
endif()

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
