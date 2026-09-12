if(NOT DEFINED HEPTA_GATEWAY_BINARY OR HEPTA_GATEWAY_BINARY STREQUAL "")
    message(FATAL_ERROR "HEPTA_GATEWAY_BINARY is required")
endif()
if(NOT EXISTS "${HEPTA_GATEWAY_BINARY}")
    message(FATAL_ERROR
        "Gateway binary does not exist: ${HEPTA_GATEWAY_BINARY}")
endif()
if(NOT DEFINED HEPTA_NM_EXECUTABLE OR HEPTA_NM_EXECUTABLE STREQUAL "")
    message(FATAL_ERROR "HEPTA_NM_EXECUTABLE is required")
endif()
execute_process(
    COMMAND "${HEPTA_NM_EXECUTABLE}" -C --defined-only
            "${HEPTA_GATEWAY_BINARY}"
    RESULT_VARIABLE HEPTA_NM_RESULT
    OUTPUT_VARIABLE HEPTA_GATEWAY_SYMBOLS
    ERROR_VARIABLE HEPTA_NM_ERROR)
if(NOT HEPTA_NM_RESULT EQUAL 0)
    message(FATAL_ERROR
        "Unable to inspect Gateway symbols with ${HEPTA_NM_EXECUTABLE}: "
        "${HEPTA_NM_ERROR}")
endif()

# Count is observability, not an arbitrary correctness or authority threshold.
string(REGEX MATCHALL "[^\r\n]+" HEPTA_GATEWAY_SYMBOL_LINES "${HEPTA_GATEWAY_SYMBOLS}")
list(LENGTH HEPTA_GATEWAY_SYMBOL_LINES HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT)

# These types belong to the privileged Execution Service implementation.  The
# Agent-facing Gateway may contain only execution contracts and client-side
# transports.  Keep the list explicit so a future target-link change fails at
# build time instead of silently widening the Gateway TCB.
set(HEPTA_GATEWAY_FORBIDDEN_SYMBOL_PATTERNS
    "UnixExecutionServiceServer::"
    "UnixExecutionEventFeedServer::"
    "ExecutionDecisionLeaseAuthority::"
    "ExecutionCoordinator::"
    "OmsJournal::"
    "HeptaIBGatewayAdapter::"
    "IBApiWrapperReal::"
    "IBAuthoritativeEventQueue::"
    "IbOrderLifecycleTracker::"
    "IbVenueCorrelationCodec::"
    "PreTradeRiskEngine::"
    "IbPaperExecutionGuard::"
    "IbPaperExecutionPolicyAuthority::"
    "IbPaperExecutionRuntimeComposition::"
    "IbPaperKillSwitch::"
    "EClient::placeOrder"
    "EClient::cancelOrder"
    "EClientSocket::")

set(HEPTA_GATEWAY_FORBIDDEN_SYMBOLS_FOUND)
foreach(HEPTA_FORBIDDEN_PATTERN
        IN LISTS HEPTA_GATEWAY_FORBIDDEN_SYMBOL_PATTERNS)
    string(FIND "${HEPTA_GATEWAY_SYMBOLS}"
        "${HEPTA_FORBIDDEN_PATTERN}" HEPTA_FORBIDDEN_OFFSET)
    if(NOT HEPTA_FORBIDDEN_OFFSET EQUAL -1)
        list(APPEND HEPTA_GATEWAY_FORBIDDEN_SYMBOLS_FOUND
            "${HEPTA_FORBIDDEN_PATTERN}")
    endif()
endforeach()

if(HEPTA_GATEWAY_FORBIDDEN_SYMBOLS_FOUND)
    list(JOIN HEPTA_GATEWAY_FORBIDDEN_SYMBOLS_FOUND ", "
        HEPTA_GATEWAY_FORBIDDEN_SYMBOLS_TEXT)
    message(FATAL_ERROR
        "Agent-facing Gateway contains privileged Execution Service symbols: "
        "${HEPTA_GATEWAY_FORBIDDEN_SYMBOLS_TEXT}")
endif()

message(STATUS
    "Gateway privileged-symbol boundary PASS: ${HEPTA_GATEWAY_BINARY}; "
    "defined_symbols=${HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT} (observed)")
