include(GNUInstallDirs)

set(HEPTA_INSTALL_SYSCONFDIR "${CMAKE_INSTALL_SYSCONFDIR}/heptatrader"
    CACHE PATH "HeptaTrader non-secret configuration directory")
set(HEPTA_INSTALL_DATADIR "${CMAKE_INSTALL_DATADIR}/heptatrader"
    CACHE PATH "HeptaTrader policy and schema directory")
set(HEPTA_INSTALL_LIBEXECDIR "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"
    CACHE PATH "HeptaTrader helper executable directory")

set(_hepta_core_runtime_targets
    hepta_tool_gatewayd
    hepta_executiond
    heptactl
    hepta_sessionctl)
foreach(_target IN LISTS _hepta_core_runtime_targets)
    if(NOT TARGET ${_target})
        message(FATAL_ERROR "canonical install target is missing: ${_target}")
    endif()
endforeach()

install(TARGETS ${_hepta_core_runtime_targets}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    COMPONENT runtime)

# A broker-linked daemon is installed only from the explicit IB SDK profile.
# The ordinary SDK-free core build cannot accidentally package a broker binary.
if(HEPTA_ENABLE_IBAPI)
    if(NOT TARGET hepta_ib_executiond)
        message(FATAL_ERROR "IB profile enabled but hepta_ib_executiond is missing")
    endif()
    install(TARGETS hepta_ib_executiond
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        COMPONENT ib-paper)
endif()

install(PROGRAMS
    adapters/mcp/hepta_mcp_server.py
    scripts/hepta_agent_mcp_launcher.py
    scripts/hepta_agent_trust_domain.py
    scripts/hepta_broker_egress_policy.py
    scripts/resolve_hepta_config.py
    scripts/verify_canonical_ib_paper_profile.py
    DESTINATION ${HEPTA_INSTALL_LIBEXECDIR}
    COMPONENT runtime)

install(FILES
    docs/capabilities.json
    docs/module-catalog.json
    docs/ib-paper-profile-policy-v1.json
    docs/OMS-EVENT-SCHEMA.md
    docs/SOURCE-STATUS.md
    DESTINATION ${HEPTA_INSTALL_DATADIR}
    COMPONENT runtime)

install(DIRECTORY docs/operations/
    DESTINATION ${CMAKE_INSTALL_DOCDIR}/operations
    COMPONENT documentation
    FILES_MATCHING PATTERN "*.md")
install(DIRECTORY docs/modules/
    DESTINATION ${CMAKE_INSTALL_DOCDIR}/modules
    COMPONENT documentation
    FILES_MATCHING PATTERN "*.md")
install(FILES
    README.md
    docs/index.md
    docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
    docs/BROKER-NETWORK-ISOLATION.md
    docs/DOCUMENTATION-POLICY.md
    docs/RUNBOOK-KILLSWITCH.md
    DESTINATION ${CMAKE_INSTALL_DOCDIR}
    COMPONENT documentation)

# Units and tmpfiles definitions are installed as deployment inputs. They do
# not create users, credentials, authorization markers or broker access.
install(DIRECTORY systemd/
    DESTINATION ${HEPTA_INSTALL_DATADIR}/systemd
    COMPONENT deployment
    FILES_MATCHING
        PATTERN "*.service"
        PATTERN "*.socket"
        PATTERN "*.target"
        PATTERN "*.example")
install(DIRECTORY tmpfiles.d/
    DESTINATION ${HEPTA_INSTALL_DATADIR}/tmpfiles.d
    COMPONENT deployment
    FILES_MATCHING PATTERN "*.conf")

install(FILES
    .env.hepta.example
    HeptaTrade/HeptaTraderConfig.xml.example
    HeptaTrade/IBRisk.template.xml
    DESTINATION ${HEPTA_INSTALL_SYSCONFDIR}/examples
    COMPONENT deployment)

file(READ "${CMAKE_SOURCE_DIR}/VERSION" _hepta_version)
string(STRIP "${_hepta_version}" _hepta_version)
if(NOT _hepta_version MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+([-.][A-Za-z0-9.]+)?$")
    message(FATAL_ERROR "VERSION is not a supported package version: ${_hepta_version}")
endif()

set(CPACK_PACKAGE_NAME "heptatrader")
set(CPACK_PACKAGE_VENDOR "TrillionniumFoundation")
set(CPACK_PACKAGE_CONTACT "HeptaTrader maintainers")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY
    "Agent-facing deterministic trading execution runtime")
set(CPACK_PACKAGE_VERSION "${_hepta_version}")
set(CPACK_PACKAGE_CHECKSUM SHA256)
set(CPACK_MONOLITHIC_INSTALL OFF)
set(CPACK_COMPONENTS_ALL runtime documentation deployment)
set(CPACK_ARCHIVE_COMPONENT_INSTALL ON)
set(CPACK_GENERATOR "TGZ")
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    list(APPEND CPACK_GENERATOR "DEB")
    set(CPACK_DEB_COMPONENT_INSTALL ON)
    set(CPACK_DEBIAN_PACKAGE_MAINTAINER "HeptaTrader maintainers")
    set(CPACK_DEBIAN_RUNTIME_PACKAGE_DEPENDS "libssl3")
    set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
endif()
include(CPack)
