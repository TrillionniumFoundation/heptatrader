include(GNUInstallDirs)

set(HEPTA_INSTALL_LIBEXECDIR "libexec")
set(HEPTA_INSTALL_SYSTEMD_UNITDIR "lib/systemd/system")
set(HEPTA_INSTALL_TMPFILESDIR "lib/tmpfiles.d")
set(HEPTA_INSTALL_DATADIR "share/heptatrader")
set(HEPTA_INSTALL_DOCDIR "share/doc/heptatrader")

install(TARGETS heptactl hepta_sessionctl
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(TARGETS hepta_tool_gatewayd hepta_executiond
    RUNTIME DESTINATION ${HEPTA_INSTALL_LIBEXECDIR})

if(TARGET hepta_paper_terminal_latch_committer)
    install(TARGETS hepta_paper_terminal_latch_committer
        RUNTIME DESTINATION ${HEPTA_INSTALL_LIBEXECDIR})
endif()

if(HEPTA_ENABLE_IBAPI)
    install(TARGETS hepta_ib_executiond
        RUNTIME DESTINATION ${HEPTA_INSTALL_LIBEXECDIR})
endif()

install(PROGRAMS adapters/mcp/hepta_mcp_server.py
    DESTINATION ${HEPTA_INSTALL_LIBEXECDIR}
    RENAME hepta-mcp-server)
install(PROGRAMS scripts/hepta_agent_mcp_launcher.py
    DESTINATION ${HEPTA_INSTALL_LIBEXECDIR}
    RENAME hepta-agent-mcp-launcher)
install(FILES scripts/hepta_agent_trust_domain.py
    DESTINATION ${HEPTA_INSTALL_LIBEXECDIR})
install(PROGRAMS scripts/hepta_observability.py
    DESTINATION ${HEPTA_INSTALL_LIBEXECDIR}
    RENAME hepta-observability)

if(HEPTA_ENABLE_IBAPI)
    install(PROGRAMS scripts/hepta_broker_egress_policy.py
        DESTINATION ${HEPTA_INSTALL_LIBEXECDIR}
        RENAME hepta-broker-egress-policy)
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(HEPTA_CORE_SYSTEMD_UNITS
        systemd/hepta-tool-gateway.service
        systemd/hepta-tool-gateway.socket
        systemd/hepta-tool-gateway@.service
        systemd/hepta-tool-gateway@.socket
        systemd/hepta-tool-session-supervisor.socket
        systemd/hepta-tool-session-supervisor@.socket
        systemd/hepta-execution-simulator.service
        systemd/hepta-execution-simulator.socket
        systemd/hepta-execution-simulator@.service
        systemd/hepta-execution-simulator@.socket
        systemd/hepta-execution-events-simulator.socket
        systemd/hepta-execution-events-simulator@.socket
        systemd/hepta-observability-simulator.service
        systemd/hepta-observability-simulator.timer)
    install(FILES ${HEPTA_CORE_SYSTEMD_UNITS}
        DESTINATION ${HEPTA_INSTALL_SYSTEMD_UNITDIR})

    if(HEPTA_ENABLE_IBAPI)
        install(FILES
            systemd/hepta-broker-egress-policy.service
            systemd/hepta-execution-ib-paper.service
            systemd/hepta-execution-ib-paper.socket
            systemd/hepta-execution-events-ib-paper.socket
            systemd/hepta-observability-ib-paper.service
            systemd/hepta-observability-ib-paper.timer
            DESTINATION ${HEPTA_INSTALL_SYSTEMD_UNITDIR})
    endif()

    install(FILES
        tmpfiles.d/heptatrader-agent-os.conf
        DESTINATION ${HEPTA_INSTALL_TMPFILESDIR})
    if(HEPTA_ENABLE_IBAPI)
        install(FILES tmpfiles.d/heptatrader-ib-paper.conf
            DESTINATION ${HEPTA_INSTALL_TMPFILESDIR})
    endif()
endif()

install(FILES
    systemd/hepta-agent-trust-domain-policy-v1.json
    systemd/hepta-service-identities-v1.json
    DESTINATION ${HEPTA_INSTALL_DATADIR})
if(HEPTA_ENABLE_IBAPI)
    install(FILES systemd/hepta-broker-network-policy-v1.json
        DESTINATION ${HEPTA_INSTALL_DATADIR})
endif()

install(DIRECTORY docs/
    DESTINATION ${HEPTA_INSTALL_DOCDIR}
    FILES_MATCHING PATTERN "*.md")
install(FILES
    README.md
    SECURITY-HARDENING.md
    VERSION
    LICENSE
    THIRD_PARTY_NOTICES.md
    DESTINATION ${HEPTA_INSTALL_DOCDIR})
install(FILES
    systemd/hepta-tool-gateway.env.example
    systemd/hepta-tool-gateway-domain.env.example
    systemd/hepta-execution-simulator.env.example
    systemd/hepta-agent-host-identity.conf.example
    systemd/hepta-agent-trust-domain.json.example
    systemd/hepta-observability-simulator.env.example
    DESTINATION ${HEPTA_INSTALL_DOCDIR}/examples)
if(HEPTA_ENABLE_IBAPI)
    install(FILES
        systemd/hepta-execution-gateway-paper.env.example
        systemd/hepta-execution-ib-paper.env.example
        systemd/hepta-agent-broker-egress-policy.conf.example
        systemd/hepta-observability-ib-paper.env.example
        DESTINATION ${HEPTA_INSTALL_DOCDIR}/examples)
endif()

set(CPACK_GENERATOR "TGZ")
set(CPACK_PACKAGE_NAME "heptatrader")
set(CPACK_PACKAGE_VENDOR "TrillionniumFoundation")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY
    "Fail-closed Agent-native trading execution runtime")
set(CPACK_PACKAGE_VERSION "${HEPTA_VERSION}")
set(CPACK_PACKAGE_FILE_NAME
    "heptatrader-${HEPTA_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
set(CPACK_INCLUDE_TOPLEVEL_DIRECTORY OFF)
set(CPACK_SET_DESTDIR ON)
set(CPACK_PACKAGING_INSTALL_PREFIX "/usr")
include(CPack)
