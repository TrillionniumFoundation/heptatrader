# Minimal runtime assembly. This component excludes release signing, host
# certification and Broker credentials. Installed documentation is the single
# canonical Documentation Control Plane V2; no compatibility aliases are shipped.

set(HEPTA_RUNTIME_LIBEXEC_DIR "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader")
set(HEPTA_RUNTIME_EXECUTABLE_DIR "${CMAKE_INSTALL_FULL_LIBEXECDIR}/heptatrader")
set(HEPTA_RUNTIME_DOC_DIR "${CMAKE_INSTALL_FULL_DOCDIR}")
set(HEPTA_GENERATED_SYSTEMD_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated/systemd")
file(MAKE_DIRECTORY "${HEPTA_GENERATED_SYSTEMD_DIR}")

foreach(HEPTA_SERVICE_TEMPLATE hepta-tool-gateway.service hepta-execution-simulator.service)
    configure_file(
        "${CMAKE_SOURCE_DIR}/systemd/${HEPTA_SERVICE_TEMPLATE}.in"
        "${HEPTA_GENERATED_SYSTEMD_DIR}/${HEPTA_SERVICE_TEMPLATE}"
        @ONLY)
endforeach()

add_custom_target(hepta_runtime_binaries DEPENDS
    hepta_tool_gatewayd hepta_executiond heptactl hepta_sessionctl)

install(TARGETS hepta_tool_gatewayd hepta_executiond
    RUNTIME DESTINATION "${HEPTA_RUNTIME_LIBEXEC_DIR}" COMPONENT runtime)
install(TARGETS heptactl hepta_sessionctl
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT runtime)
install(PROGRAMS "${CMAKE_SOURCE_DIR}/adapters/mcp/hepta_mcp_server.py"
    DESTINATION "${HEPTA_RUNTIME_LIBEXEC_DIR}" RENAME hepta-mcp-server COMPONENT runtime)
install(PROGRAMS "${CMAKE_SOURCE_DIR}/scripts/hepta_agent_mcp_launcher.py"
    DESTINATION "${CMAKE_INSTALL_BINDIR}" RENAME hepta-agent-mcp-launcher COMPONENT runtime)
install(FILES "${CMAKE_SOURCE_DIR}/scripts/hepta_agent_trust_domain.py"
    DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT runtime)

install(FILES
    "${HEPTA_GENERATED_SYSTEMD_DIR}/hepta-tool-gateway.service"
    "${HEPTA_GENERATED_SYSTEMD_DIR}/hepta-execution-simulator.service"
    "${CMAKE_SOURCE_DIR}/systemd/hepta-tool-gateway.socket"
    "${CMAKE_SOURCE_DIR}/systemd/hepta-tool-session-supervisor.socket"
    "${CMAKE_SOURCE_DIR}/systemd/hepta-execution-simulator.socket"
    "${CMAKE_SOURCE_DIR}/systemd/hepta-execution-events-simulator.socket"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/systemd/system" COMPONENT runtime)
install(FILES "${CMAKE_SOURCE_DIR}/tmpfiles.d/heptatrader-agent-os.conf"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/tmpfiles.d" COMPONENT runtime)
install(FILES "${CMAKE_SOURCE_DIR}/sysusers.d/heptatrader.conf"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/sysusers.d" COMPONENT runtime)

install(FILES
    "${CMAKE_SOURCE_DIR}/systemd/hepta-tool-gateway.env.example"
    "${CMAKE_SOURCE_DIR}/systemd/hepta-execution-simulator.env.example"
    "${CMAKE_SOURCE_DIR}/systemd/hepta-agent-trust-domain.json.example"
    "${CMAKE_SOURCE_DIR}/HeptaTrade/HeptaTraderConfig.xml.example"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/examples" COMPONENT runtime)
install(DIRECTORY "${CMAKE_SOURCE_DIR}/plugins/heptatrader-agent-os/"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/plugins/heptatrader-agent-os"
    COMPONENT runtime FILES_MATCHING PATTERN "*.json" PATTERN "README.md")

install(FILES "${CMAKE_SOURCE_DIR}/research/manifest-v1.json"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/research" COMPONENT runtime)
install(PROGRAMS "${CMAKE_SOURCE_DIR}/research/run_protocol.py"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/research" COMPONENT runtime)
install(FILES
    "${CMAKE_SOURCE_DIR}/research/protocol_support.py"
    "${CMAKE_SOURCE_DIR}/research/__init__.py"
    "${CMAKE_SOURCE_DIR}/research/README.md"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/research" COMPONENT runtime)

# Install the complete registered canonical documentation graph. The repository
# checker forbids aliases, historical directories and unregistered files.
install(DIRECTORY "${CMAKE_SOURCE_DIR}/docs/"
    DESTINATION "${CMAKE_INSTALL_DOCDIR}"
    COMPONENT runtime
    FILES_MATCHING PATTERN "*.md" PATTERN "*.json")

install(DIRECTORY "${CMAKE_SOURCE_DIR}/schemas/"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/schemas"
    COMPONENT runtime FILES_MATCHING PATTERN "*.json" PATTERN "*.sha256")
