include_guard(GLOBAL)
include(GNUInstallDirs)

if(NOT DEFINED HEPTA_RELEASE_LABEL OR HEPTA_RELEASE_LABEL STREQUAL "")
    set(HEPTA_RELEASE_LABEL "${PROJECT_VERSION}")
endif()

if(HEPTA_ENABLE_IBAPI)
    set(HEPTA_IBAPI_COMPILED_JSON true)
else()
    set(HEPTA_IBAPI_COMPILED_JSON false)
endif()

configure_file(
    "${PROJECT_SOURCE_DIR}/cmake/heptatrader-build-info.json.in"
    "${PROJECT_BINARY_DIR}/heptatrader-build-info.json"
    @ONLY)

set(_hepta_runtime_targets
    heptactl
    hepta_sessionctl
    hepta_tool_gatewayd
    hepta_executiond)

if(HEPTA_ENABLE_IBAPI)
    list(APPEND _hepta_runtime_targets hepta_ib_executiond)
endif()
if(TARGET hepta_paper_terminal_latch_committer)
    list(APPEND _hepta_runtime_targets hepta_paper_terminal_latch_committer)
endif()

foreach(_hepta_target IN LISTS _hepta_runtime_targets)
    if(NOT TARGET "${_hepta_target}")
        message(FATAL_ERROR "canonical install target is missing: ${_hepta_target}")
    endif()
    install(TARGETS "${_hepta_target}"
        RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
        COMPONENT runtime)
endforeach()

install(PROGRAMS
    "${PROJECT_SOURCE_DIR}/scripts/hepta_preflight.py"
    DESTINATION "${CMAKE_INSTALL_BINDIR}"
    RENAME hepta-preflight
    COMPONENT runtime)

# The parser/verification implementation is a private library module. It is
# loaded by the descriptor-pinned public wrapper and is never installed in the
# public binary namespace.
install(FILES
    "${PROJECT_SOURCE_DIR}/scripts/hepta_preflight_core.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"
    RENAME hepta-preflight-core.py
    COMPONENT runtime)

set(_hepta_runtime_helpers
    scripts/hepta_agent_mcp_launcher.py
    scripts/hepta_agent_trust_domain.py
    scripts/hepta_broker_egress_policy.py
    scripts/resolve_hepta_config.py
    scripts/validate_sim_data.py
    scripts/verify_canonical_ib_paper_profile.py
    scripts/verify_oms_journal_replay.py
    adapters/mcp/hepta_mcp_server.py)
foreach(_hepta_helper IN LISTS _hepta_runtime_helpers)
    if(NOT EXISTS "${PROJECT_SOURCE_DIR}/${_hepta_helper}")
        message(FATAL_ERROR "canonical runtime helper is missing: ${_hepta_helper}")
    endif()
endforeach()
install(PROGRAMS ${_hepta_runtime_helpers}
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"
    COMPONENT runtime)

install(FILES
    "${PROJECT_BINARY_DIR}/heptatrader-build-info.json"
    "${PROJECT_SOURCE_DIR}/docs/capabilities.json"
    "${PROJECT_SOURCE_DIR}/docs/ib-paper-profile-policy-v1.json"
    "${PROJECT_SOURCE_DIR}/docs/preflight-policy-v1.json"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader"
    COMPONENT runtime)

if(HEPTA_ENABLE_IBAPI)
    install(FILES
        "${PROJECT_SOURCE_DIR}/systemd/hepta-broker-network-policy-v1.json"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader"
        COMPONENT runtime)
endif()

install(DIRECTORY "${PROJECT_SOURCE_DIR}/systemd/"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/systemd/system"
    COMPONENT runtime
    FILES_MATCHING
        PATTERN "*.service"
        PATTERN "*.socket"
        PATTERN "*.target"
        PATTERN "*.path"
        PATTERN "*.timer")

install(DIRECTORY "${PROJECT_SOURCE_DIR}/systemd/"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/examples/systemd"
    COMPONENT examples
    FILES_MATCHING
        PATTERN "*.example"
        PATTERN "*.json")

install(DIRECTORY "${PROJECT_SOURCE_DIR}/tmpfiles.d/"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/tmpfiles.d"
    COMPONENT runtime
    FILES_MATCHING PATTERN "*.conf")

if(EXISTS "${PROJECT_SOURCE_DIR}/README.md")
    install(FILES "${PROJECT_SOURCE_DIR}/README.md"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/doc/heptatrader"
        COMPONENT documentation)
endif()
install(DIRECTORY "${PROJECT_SOURCE_DIR}/docs/"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/doc/heptatrader"
    COMPONENT documentation
    FILES_MATCHING
        PATTERN "*.md"
        PATTERN "*.json")

unset(_hepta_runtime_targets)
unset(_hepta_runtime_helpers)
unset(_hepta_target)
unset(_hepta_helper)
