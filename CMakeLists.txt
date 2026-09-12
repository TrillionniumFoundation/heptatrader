cmake_minimum_required(VERSION 3.16)
# VERSION is the sole release-label authority.  CI, packaging and installed
# build metadata all consume this value instead of carrying independent
# literals that can silently drift.
file(STRINGS "${CMAKE_SOURCE_DIR}/VERSION" HEPTA_RELEASE_LABEL
     LIMIT_COUNT 1)
string(STRIP "${HEPTA_RELEASE_LABEL}" HEPTA_RELEASE_LABEL)
string(LENGTH "${HEPTA_RELEASE_LABEL}" _hepta_release_label_length)
if(NOT HEPTA_RELEASE_LABEL MATCHES "^[A-Za-z0-9][A-Za-z0-9._-]*$"
   OR _hepta_release_label_length GREATER 64)
    message(FATAL_ERROR "VERSION does not contain a valid release label")
endif()
if(NOT HEPTA_RELEASE_LABEL MATCHES "^([0-9]+)\\.([0-9]+)\\.([0-9]+)")
    message(FATAL_ERROR "VERSION must start with major.minor.patch")
endif()
set(_hepta_project_version "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}.${CMAKE_MATCH_3}")
project(HeptaTrader VERSION "${_hepta_project_version}" LANGUAGES CXX)
set(HEPTA_RELEASE_LABEL "${HEPTA_RELEASE_LABEL}" CACHE STRING
    "Version label recorded in installed HeptaTrader build metadata" FORCE)
option(BUILD_IB_PROBE "Build the IB/TWS connectivity probe" OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue composition binary" OFF)
option(HEPTA_BUILD_LEGACY_SIMULATOR
       "Build the deprecated Pegasus simulator" OFF)

include(CTest)

# Instrumented libraries must retain RTTI for UBSan's vptr checks. Detect both
# compile and link flags because supported validation profiles set both, while
# custom sanitizer builds may specify only one side. The ordinary Release
# executable keeps its reviewed optimization and quantitative symbol budget.
set(HEPTA_INSTRUMENTED_BUILD OFF)
set(_hepta_instrumentation_flags
    "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_RELEASE} ${CMAKE_EXE_LINKER_FLAGS} ${CMAKE_EXE_LINKER_FLAGS_RELEASE} ${CMAKE_SHARED_LINKER_FLAGS} ${CMAKE_SHARED_LINKER_FLAGS_RELEASE}")
if(_hepta_instrumentation_flags MATCHES "(^|[ ;])-fsanitize[= ]")
    set(HEPTA_INSTRUMENTED_BUILD ON)
endif()

if(HEPTA_BUILD_LEGACY_MONOLITH)
    add_subdirectory(HeptaStrategy)
endif()

add_subdirectory(HeptaTrade)

# Preserve RTTI for instrumented Release builds. Removing the optimization
# unconditionally also expanded the ordinary production Gateway beyond its
# reviewed symbol budget; the verifier must remain active for both variants.
function(hepta_remove_legacy_no_rtti target)
    get_target_property(_hepta_compile_options ${target} COMPILE_OPTIONS)
    if(_hepta_compile_options AND
       NOT _hepta_compile_options STREQUAL "_hepta_compile_options-NOTFOUND")
        list(REMOVE_ITEM _hepta_compile_options
             "$<$<CONFIG:Release>:-fno-rtti>")
        set_property(TARGET ${target} PROPERTY COMPILE_OPTIONS
                     "${_hepta_compile_options}")
    endif()
endfunction()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND HEPTA_INSTRUMENTED_BUILD)
    hepta_remove_legacy_no_rtti(hepta_agent_os_core)
    hepta_remove_legacy_no_rtti(hepta_tool_gatewayd)
endif()

if(HEPTA_BUILD_LEGACY_SIMULATOR)
    add_subdirectory(HeptaSimulator)
endif()

if(BUILD_IB_PROBE)
    add_subdirectory(docs/ib_probe)
endif()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()

# Install ownership is registered after optional test targets exist so a
# canonical tested release can carry the installed simulator lifecycle smoke.
include(cmake/HeptaInstall.cmake)
