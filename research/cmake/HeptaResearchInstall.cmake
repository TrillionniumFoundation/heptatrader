# The portable SDK is a separate developer artifact. Never extend the canonical
# runtime install/package manifest by including this from a root build.
if(NOT HEPTA_RESEARCH_STANDALONE)
    message(FATAL_ERROR "HeptaResearch installation requires the standalone research build")
endif()
option(HEPTA_RESEARCH_INSTALL_SDK "Install the offline developer SDK" ON)
if(NOT HEPTA_RESEARCH_INSTALL_SDK)
    return()
endif()
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# Absolute GNUInstallDirs values defeat prefix relocation and DESTDIR staging.
foreach(_dir CMAKE_INSTALL_INCLUDEDIR CMAKE_INSTALL_LIBDIR CMAKE_INSTALL_BINDIR CMAKE_INSTALL_DATADIR)
    if("${${_dir}}" STREQUAL "" OR IS_ABSOLUTE "${${_dir}}" OR
       "${${_dir}}" MATCHES "(^|[/\\\\])\\.\\.([/\\\\]|$)")
        message(FATAL_ERROR "${_dir} must be a nonempty relative path without parent traversal")
    endif()
endforeach()
file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/../VERSION" HEPTA_RESEARCH_RELEASE_LABEL LIMIT_COUNT 1)
string(STRIP "${HEPTA_RESEARCH_RELEASE_LABEL}" HEPTA_RESEARCH_RELEASE_LABEL)
string(LENGTH "${HEPTA_RESEARCH_RELEASE_LABEL}" _label_length)
if(NOT HEPTA_RESEARCH_RELEASE_LABEL MATCHES "^[A-Za-z0-9][A-Za-z0-9._-]*$" OR _label_length GREATER 64)
    message(FATAL_ERROR "Invalid repository VERSION")
endif()
if(NOT HEPTA_RESEARCH_RELEASE_LABEL MATCHES "^([0-9]+)\\.([0-9]+)\\.([0-9]+)")
    message(FATAL_ERROR "VERSION must start with major.minor.patch")
endif()
set(HEPTA_RESEARCH_PACKAGE_VERSION "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}.${CMAKE_MATCH_3}")

# The VERSION label is not a source identity or a statement of ABI compatibility.
set(HEPTA_RESEARCH_SOURCE_SHA "unavailable")
set(HEPTA_RESEARCH_SOURCE_STATE "unavailable")
find_package(Git QUIET)
if(GIT_FOUND)
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${CMAKE_CURRENT_SOURCE_DIR}/.." rev-parse HEAD
        RESULT_VARIABLE _git_result OUTPUT_VARIABLE _git_sha OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    string(LENGTH "${_git_sha}" _sha_length)
    if(_git_result EQUAL 0 AND _sha_length EQUAL 40 AND _git_sha MATCHES "^[0-9a-f]+$")
        set(HEPTA_RESEARCH_SOURCE_SHA "${_git_sha}")
        execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${CMAKE_CURRENT_SOURCE_DIR}/.."
            status --porcelain --untracked-files=normal
            RESULT_VARIABLE _status_result OUTPUT_VARIABLE _git_status ERROR_QUIET)
        if(_status_result EQUAL 0 AND "${_git_status}" STREQUAL "")
            set(HEPTA_RESEARCH_SOURCE_STATE "clean")
        else()
            set(HEPTA_RESEARCH_SOURCE_STATE "dirty-or-unverifiable")
        endif()
    endif()
endif()

set(_offline_targets hepta_research_data hepta_research_analytics hepta_research_replay hepta_research_strategy)
set(_export_names Data Analytics Replay Strategy)
foreach(_index RANGE 0 3)
    list(GET _offline_targets ${_index} _target)
    list(GET _export_names ${_index} _export_name)
    set_target_properties(${_target} PROPERTIES EXPORT_NAME "${_export_name}")
    # Keep the target's own build include directory; only its public interface
    # changes between build-tree and install-tree consumption.
    set_property(TARGET ${_target} PROPERTY INTERFACE_INCLUDE_DIRECTORIES
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>;$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>")
endforeach()
set(_config_dir "${CMAKE_INSTALL_LIBDIR}/cmake/HeptaResearch")
configure_package_config_file("${CMAKE_CURRENT_LIST_DIR}/HeptaResearchConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/HeptaResearchConfig.cmake"
    INSTALL_DESTINATION "${_config_dir}")
write_basic_package_version_file("${CMAKE_CURRENT_BINARY_DIR}/HeptaResearchConfigVersion.cmake"
    VERSION "${HEPTA_RESEARCH_PACKAGE_VERSION}" COMPATIBILITY ExactVersion)
configure_file("${CMAKE_CURRENT_LIST_DIR}/sdk-build-info.txt.in"
    "${CMAKE_CURRENT_BINARY_DIR}/sdk-build-info.txt" @ONLY)

install(TARGETS ${_offline_targets} EXPORT HeptaResearchTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}" COMPONENT ResearchSDK)
install(TARGETS hepta_research_replay_cli
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT ResearchSDK)
# Explicit public header allowlist: the native client is NOT an offline SDK API.
install(FILES
    include/hepta/research/market_data.h
    include/hepta/research/analytics.h
    include/hepta/research/replay.h
    include/hepta/research/strategy.h
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/hepta/research" COMPONENT ResearchSDK)
install(EXPORT HeptaResearchTargets NAMESPACE HeptaResearch::
    DESTINATION "${_config_dir}" COMPONENT ResearchSDK)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/HeptaResearchConfig.cmake"
              "${CMAKE_CURRENT_BINARY_DIR}/HeptaResearchConfigVersion.cmake"
    DESTINATION "${_config_dir}" COMPONENT ResearchSDK)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/sdk-build-info.txt" PACKAGE.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/HeptaResearch" COMPONENT ResearchSDK)
install(FILES examples/ticks.csv examples/sessions.csv
    DESTINATION "${CMAKE_INSTALL_DATADIR}/HeptaResearch/examples" COMPONENT ResearchSDK)

if(BUILD_TESTING)
    add_test(NAME hepta_research_sdk_install COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/sdk_package_behavior.py"
        "--cmake=${CMAKE_COMMAND}" "--build-dir=${CMAKE_CURRENT_BINARY_DIR}"
        "--source-dir=${CMAKE_CURRENT_SOURCE_DIR}" "--config=$<CONFIG>")
    set_tests_properties(hepta_research_sdk_install PROPERTIES LABELS "core;research;install" TIMEOUT 120)
endif()
