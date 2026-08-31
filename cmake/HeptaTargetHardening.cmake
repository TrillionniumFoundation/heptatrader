if(NOT HEPTA_ENABLE_HARDENING)
    return()
endif()

set(HEPTA_RUNTIME_HARDENING_TARGETS
    heptactl
    hepta_sessionctl
    hepta_paper_terminal_latch_committer
    hepta_tool_gatewayd
    hepta_executiond
    hepta_ib_executiond
    hepta_native_tool_client
    hepta_execution_contract
    hepta_execution_transport
    hepta_execution_client
    hepta_execution_server
    hepta_agent_execution_support
    hepta_execution_core
    hepta_trading_tool_core
    hepta_agent_os_core)

foreach(target IN LISTS HEPTA_RUNTIME_HARDENING_TARGETS)
    if(NOT TARGET ${target})
        continue()
    endif()

    set_target_properties(${target} PROPERTIES
        CXX_STANDARD 11
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF
        POSITION_INDEPENDENT_CODE ON)

    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(${target} PRIVATE
            -fstack-protector-strong
            -fno-common
            -fno-strict-overflow
            -fno-delete-null-pointer-checks
            -Wformat-security
            -Werror=format-security)
        target_compile_definitions(${target} PRIVATE
            $<$<CONFIG:Release>:_FORTIFY_SOURCE=2>)

        get_target_property(target_type ${target} TYPE)
        if(target_type STREQUAL "EXECUTABLE" AND UNIX AND NOT APPLE)
            target_link_options(${target} PRIVATE
                -Wl,-z,relro
                -Wl,-z,now
                -Wl,--as-needed)
        endif()
    endif()
endforeach()
