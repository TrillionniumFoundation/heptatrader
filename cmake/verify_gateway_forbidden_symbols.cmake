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
if(NOT DEFINED HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET)
    message(FATAL_ERROR
        "HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET must be explicitly ON or OFF")
endif()
if(NOT HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET STREQUAL "ON"
        AND NOT HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET STREQUAL "OFF")
    message(FATAL_ERROR
        "HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET must be exactly ON or OFF")
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

# A bounded total symbol surface catches accidental whole-library linkage even
# when a newly introduced privileged type has not yet acquired an explicit
# deny-list pattern. Exact-head Release evidence after the C++17,
# target-scoped runtime split measured 1420 defined symbols. The 1500-symbol
# baseline leaves bounded review headroom while keeping the deny-list below
# non-negotiable. Future increases require a fresh exact-head measurement and
# an ADR update. Debug and sanitizer instrumentation expand the generated
# symbol surface, so those builds run the deny-list but not this Release budget.
set(HEPTA_GATEWAY_MAX_DEFINED_SYMBOLS 1500)
string(REGEX MATCHALL "[^\r\n]+" HEPTA_GATEWAY_SYMBOL_LINES
    "${HEPTA_GATEWAY_SYMBOLS}")
list(LENGTH HEPTA_GATEWAY_SYMBOL_LINES HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT)
if(HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET STREQUAL "ON"
        AND HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT GREATER
            HEPTA_GATEWAY_MAX_DEFINED_SYMBOLS)
    message(FATAL_ERROR
        "Agent-facing Gateway defined-symbol budget exceeded: "
        "${HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT} > "
        "${HEPTA_GATEWAY_MAX_DEFINED_SYMBOLS}")
endif()

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

if(HEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET STREQUAL "ON")
    set(HEPTA_GATEWAY_SYMBOL_BUDGET_STATUS
        "${HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT}/${HEPTA_GATEWAY_MAX_DEFINED_SYMBOLS} enforced")
else()
    set(HEPTA_GATEWAY_SYMBOL_BUDGET_STATUS
        "${HEPTA_GATEWAY_DEFINED_SYMBOL_COUNT} observed; Release-only quantitative budget not enforced")
endif()
message(STATUS
    "Gateway privileged-symbol boundary PASS: ${HEPTA_GATEWAY_BINARY}; "
    "defined_symbols=${HEPTA_GATEWAY_SYMBOL_BUDGET_STATUS}")
