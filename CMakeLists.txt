cmake_minimum_required(VERSION 3.16)
project(HeptaTrader LANGUAGES CXX)

option(BUILD_IB_PROBE "Build the IB/TWS connectivity probe" OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue composition binary" OFF)
option(HEPTA_BUILD_LEGACY_SIMULATOR
       "Build the deprecated Pegasus simulator" OFF)

include(CTest)

if(HEPTA_BUILD_LEGACY_MONOLITH)
    add_subdirectory(HeptaStrategy)
endif()

add_subdirectory(HeptaTrade)

# The subproject historically disabled RTTI in Release to reduce the Gateway
# symbol surface. That optimization is not a privilege boundary, conflicts
# with Clang UBSan's vptr/type checks, and made the reliability suite fail at
# link time. Remove the target-local option after composition; the explicit
# forbidden-symbol verifier remains the actual Gateway boundary.
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

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
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
