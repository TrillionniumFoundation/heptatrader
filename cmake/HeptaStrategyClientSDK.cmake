# Developer-only export of the actual canonical client targets. Every install
# rule is excluded from default/all-component production installation.
include_guard(GLOBAL)
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

if(HEPTA_RESEARCH_STANDALONE OR NOT TARGET hepta_research_native_client)
    message(FATAL_ERROR "The strategy-client SDK requires the canonical root build")
endif()
foreach(_dir CMAKE_INSTALL_INCLUDEDIR CMAKE_INSTALL_LIBDIR CMAKE_INSTALL_DATADIR CMAKE_INSTALL_BINDIR)
    if("${${_dir}}" STREQUAL "" OR IS_ABSOLUTE "${${_dir}}" OR
       "${${_dir}}" MATCHES "(^|[/\\])\\.\\.([/\\]|$)")
        message(FATAL_ERROR "StrategyClientSDK requires relative ${_dir} without parent traversal")
    endif()
endforeach()
set(_sdk_include "${CMAKE_INSTALL_INCLUDEDIR}/HeptaStrategyClient")
set(_sdk_config "${CMAKE_INSTALL_LIBDIR}/cmake/HeptaStrategyClient")
set_target_properties(hepta_research_native_client PROPERTIES EXPORT_NAME Client)
set_target_properties(hepta_typed_tool_protocol PROPERTIES EXPORT_NAME ToolProtocol)
set_target_properties(hepta_unix_tool_client PROPERTIES EXPORT_NAME UnixToolClient)
set_property(TARGET hepta_research_native_client PROPERTY INTERFACE_INCLUDE_DIRECTORIES
    "$<BUILD_INTERFACE:${PROJECT_SOURCE_DIR}/research/include>;$<INSTALL_INTERFACE:${_sdk_include}>")
foreach(_target hepta_native_tool_client hepta_typed_tool_protocol hepta_unix_tool_client)
    set_property(TARGET ${_target} PROPERTY INTERFACE_INCLUDE_DIRECTORIES
        "$<BUILD_INTERFACE:${PROJECT_SOURCE_DIR}/HeptaTrade>;$<INSTALL_INTERFACE:${_sdk_include}>")
endforeach()

set(HEPTA_STRATEGY_SDK_SOURCE_SHA "unavailable")
set(HEPTA_STRATEGY_SDK_SOURCE_STATE "unavailable")
find_package(Git QUIET)
if(GIT_FOUND)
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${PROJECT_SOURCE_DIR}" rev-parse HEAD
        RESULT_VARIABLE _git_result OUTPUT_VARIABLE _git_sha
        OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    string(LENGTH "${_git_sha}" _git_sha_length)
    if(_git_result EQUAL 0 AND _git_sha_length EQUAL 40 AND _git_sha MATCHES "^[0-9a-f]+$")
        set(HEPTA_STRATEGY_SDK_SOURCE_SHA "${_git_sha}")
        execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${PROJECT_SOURCE_DIR}"
            status --porcelain --untracked-files=normal
            RESULT_VARIABLE _status_result OUTPUT_VARIABLE _status ERROR_QUIET)
        if(_status_result EQUAL 0 AND "${_status}" STREQUAL "")
            set(HEPTA_STRATEGY_SDK_SOURCE_STATE "clean")
        else()
            set(HEPTA_STRATEGY_SDK_SOURCE_STATE "dirty-or-unverifiable")
        endif()
    endif()
endif()
configure_package_config_file("${CMAKE_CURRENT_LIST_DIR}/HeptaStrategyClientConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/HeptaStrategyClientConfig.cmake"
    INSTALL_DESTINATION "${_sdk_config}")
write_basic_package_version_file("${CMAKE_CURRENT_BINARY_DIR}/HeptaStrategyClientConfigVersion.cmake"
    VERSION "${PROJECT_VERSION}" COMPATIBILITY ExactVersion)
configure_file("${CMAKE_CURRENT_LIST_DIR}/strategy-client-build-info.txt.in"
    "${CMAKE_CURRENT_BINARY_DIR}/strategy-client-build-info.txt" @ONLY)

install(TARGETS hepta_research_native_client hepta_native_tool_client hepta_typed_tool_protocol hepta_unix_tool_client
    EXPORT HeptaStrategyClientTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
# Exact public dependency closure, not a recursive copy of the runtime headers.
install(FILES "${PROJECT_SOURCE_DIR}/research/include/hepta/research/native_strategy_client.h"
    DESTINATION "${_sdk_include}/hepta/research" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${PROJECT_SOURCE_DIR}/HeptaTrade/client/native_tool_client.h"
              "${PROJECT_SOURCE_DIR}/HeptaTrade/client/native_tool_discovery_contract.h"
    DESTINATION "${_sdk_include}/client" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${PROJECT_SOURCE_DIR}/HeptaTrade/execution/trading_contract.h"
    DESTINATION "${_sdk_include}/execution" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${PROJECT_SOURCE_DIR}/HeptaTrade/tool_host/trading_tool_request.h"
              "${PROJECT_SOURCE_DIR}/HeptaTrade/tool_host/typed_tool_protocol.h"
    DESTINATION "${_sdk_include}/tool_host" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${PROJECT_SOURCE_DIR}/HeptaTrade/tools/trading_tool_types.h"
              "${PROJECT_SOURCE_DIR}/HeptaTrade/tools/trading_tool_wire_contract.h"
    DESTINATION "${_sdk_include}/tools" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(EXPORT HeptaStrategyClientTargets NAMESPACE HeptaStrategyClient::
    DESTINATION "${_sdk_config}" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/HeptaStrategyClientConfig.cmake"
              "${CMAKE_CURRENT_BINARY_DIR}/HeptaStrategyClientConfigVersion.cmake"
    DESTINATION "${_sdk_config}" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/strategy-client-build-info.txt"
              "${PROJECT_SOURCE_DIR}/research/CLIENT_PACKAGE.md"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/HeptaStrategyClient"
    COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)

# Both entry points are explicit developer-component installs, never production.
install(TARGETS hepta_strategy_client_cli RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${PROJECT_SOURCE_DIR}/research/strategy_gateway.py"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/HeptaStrategyClient"
    COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
install(FILES "${PROJECT_SOURCE_DIR}/research/STRATEGY-GATEWAY.md"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/HeptaStrategyClient"
    COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)
file(RELATIVE_PATH HEPTA_APP_MODULE_FROM_BIN "/${CMAKE_INSTALL_BINDIR}"
    "/${CMAKE_INSTALL_DATADIR}/HeptaStrategyClient/strategy_gateway.py")
configure_file("${PROJECT_SOURCE_DIR}/research/cmake/strategy_gateway_main.py.in"
    "${CMAKE_CURRENT_BINARY_DIR}/hepta-strategy-gateway" @ONLY)
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/hepta-strategy-gateway"
    DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT StrategyClientSDK EXCLUDE_FROM_ALL)

if(BUILD_TESTING)
    add_test(NAME hepta_research_native_sdk_install COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/research/native_sdk_package_behavior.py"
        "--cmake=${CMAKE_COMMAND}" "--build-dir=${PROJECT_BINARY_DIR}"
        "--source-dir=${PROJECT_SOURCE_DIR}" "--config=$<CONFIG>")
    set_tests_properties(hepta_research_native_sdk_install PROPERTIES
        LABELS "core;research;install" TIMEOUT 120)
endif()
