# Explicit opt-in historical target. Never included by the canonical profile.
# Source paths are relative to the including HeptaTrade source directory.
if(NOT HEPTA_BUILD_LEGACY_MONOLITH)
    message(FATAL_ERROR "HeptaLegacy.cmake requires HEPTA_BUILD_LEGACY_MONOLITH=ON")
endif()

set(DIR_SRC
    HeptaDemoStrategyTrader.cpp
    ib_fx_multi_strategy.cpp
    oms_journal.cpp
    oms_recover.cpp
    openclaw_0dte_bridge.cpp
    order_watchdog.cpp
    adapter_ib/ib_api_wrapper.cpp
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
    ${HEPTA_SESSION_SUPERVISOR_CORE_SOURCES}
    tool_host/tool_decision_audit.cpp
    ${HEPTA_TOOL_HOST_CORE_SOURCES}
    tool_host/trading_tool_session_lifecycle.cpp
    tool_host/trading_tool_session_recovery.cpp
    tool_host/trading_tool_session_catalog.cpp
    tool_host/trading_tool_session_control_plane.cpp
    tool_host/trading_tool_watch_transaction.cpp
    ${HEPTA_TYPED_TOOL_PROTOCOL_SOURCES}
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
    state/snapshot_refresh_coordinator.cpp)

if(HEPTA_BUILD_LEGACY_MONOLITH)
    add_library(CTPTradeLIB UNKNOWN IMPORTED)
    set_property(TARGET CTPTradeLIB PROPERTY IMPORTED_LOCATION
        "${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/thosttraderapi_se.so")
    add_library(CTPMdLIB UNKNOWN IMPORTED)
    set_property(TARGET CTPMdLIB PROPERTY IMPORTED_LOCATION
        "${PROJECT_SOURCE_DIR}/../Interface/CTPTradeApiLinux/thostmduserapi_se.so")

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

add_executable(HeptaTrader ${DIR_SRC})
