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

# The dynamic release smoke executes the same built binary as the canonical
# core CTest. BUILD_TESTING=ON is part of the supported release profile.
if(TARGET hepta_agent_simulator_e2e_tests)
    install(TARGETS hepta_agent_simulator_e2e_tests
        RUNTIME DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"
        COMPONENT smoke)
endif()

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
    scripts/run_release_simulator_smoke.py
    scripts/verify_canonical_ib_paper_profile.py
    scripts/hepta_oms_report.py
    scripts/hepta_telemetry_collect.py
    scripts/oms_archive_codec.py
    scripts/hepta_oms_archive.py
    scripts/hepta_oms_checkpoint.py
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

# Install only runnable units for the selected profile. Examples remain inert.
file(GLOB _hepta_units CONFIGURE_DEPENDS
    "${PROJECT_SOURCE_DIR}/systemd/*.service"
    "${PROJECT_SOURCE_DIR}/systemd/*.socket"
    "${PROJECT_SOURCE_DIR}/systemd/*.target"
    "${PROJECT_SOURCE_DIR}/systemd/*.path"
    "${PROJECT_SOURCE_DIR}/systemd/*.timer")
if(NOT HEPTA_ENABLE_IBAPI)
    list(FILTER _hepta_units EXCLUDE REGEX "(ib-paper|broker-egress-policy)\\.(service|socket)$")
endif()
install(FILES ${_hepta_units}
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/systemd/system"
    COMPONENT runtime)
unset(_hepta_units)

install(DIRECTORY "${PROJECT_SOURCE_DIR}/systemd/"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/examples/systemd"
    COMPONENT examples
    FILES_MATCHING
        PATTERN "*.example"
        PATTERN "*.json")

install(FILES "${PROJECT_SOURCE_DIR}/tmpfiles.d/heptatrader-agent-os.conf"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/tmpfiles.d"
    COMPONENT runtime)
if(HEPTA_ENABLE_IBAPI)
    install(FILES "${PROJECT_SOURCE_DIR}/tmpfiles.d/heptatrader-ib-paper.conf"
        DESTINATION "${CMAKE_INSTALL_LIBDIR}/tmpfiles.d"
        COMPONENT runtime)
endif()

# Keep the public documentation namespace stable, but render source-relative
# links for that installed layout. Out-of-package references bind this source.
find_package(Python3 COMPONENTS Interpreter REQUIRED)
set(HEPTA_DOCUMENTATION_SOURCE_SHA "" CACHE STRING
    "Exact source SHA for documentation links when building from a source archive")
if(HEPTA_DOCUMENTATION_SOURCE_SHA STREQUAL "")
    execute_process(COMMAND git rev-parse HEAD WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}"
        RESULT_VARIABLE _hepta_git_result OUTPUT_VARIABLE _hepta_doc_sha
        OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(NOT _hepta_git_result EQUAL 0)
        message(FATAL_ERROR "Supply HEPTA_DOCUMENTATION_SOURCE_SHA for an exported source build")
    endif()
else()
    set(_hepta_doc_sha "${HEPTA_DOCUMENTATION_SOURCE_SHA}")
endif()
file(GLOB_RECURSE _hepta_doc_inputs CONFIGURE_DEPENDS
    "${PROJECT_SOURCE_DIR}/docs/*.md" "${PROJECT_SOURCE_DIR}/docs/*.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    ${_hepta_doc_inputs} "${PROJECT_SOURCE_DIR}/README.md"
    "${PROJECT_SOURCE_DIR}/cmake/render_installed_documentation.py")
execute_process(COMMAND "${Python3_EXECUTABLE}"
    "${PROJECT_SOURCE_DIR}/cmake/render_installed_documentation.py"
    --source "${PROJECT_SOURCE_DIR}"
    --output "${PROJECT_BINARY_DIR}/installed-documentation"
    --source-sha "${_hepta_doc_sha}"
    RESULT_VARIABLE _hepta_doc_result)
if(NOT _hepta_doc_result EQUAL 0)
    message(FATAL_ERROR "Installed documentation generation failed")
endif()
install(DIRECTORY "${PROJECT_BINARY_DIR}/installed-documentation/"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/doc/heptatrader"
    COMPONENT documentation)


unset(_hepta_runtime_targets)
unset(_hepta_runtime_helpers)
unset(_hepta_target)
unset(_hepta_helper)
