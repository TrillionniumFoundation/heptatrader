# Canonical ownership and executable composition for bounded header-only
# components. These controls remain bounded implementations; assigning their
# headers to production library targets does not claim a process sandbox,
# distributed control plane, persistent feature service, or realistic venue.

foreach(target
        hepta_feature_runtime
        hepta_management_control
        hepta_multi_agent_simulator
        hepta_strategy_runtime)
    if(NOT TARGET ${target})
        message(FATAL_ERROR
            "Bounded runtime composition requires canonical target ${target}")
    endif()
endforeach()

target_sources(hepta_feature_runtime PRIVATE
    "${CMAKE_SOURCE_DIR}/HeptaTrade/features/feature_graph.h")
target_sources(hepta_management_control PRIVATE
    "${CMAKE_SOURCE_DIR}/HeptaTrade/management/durable_rollout_store.h")
target_sources(hepta_multi_agent_simulator PRIVATE
    "${CMAKE_SOURCE_DIR}/HeptaTrade/simulator/multi_agent_allocation_scenario.h")
target_sources(hepta_strategy_runtime PRIVATE
    "${CMAKE_SOURCE_DIR}/HeptaTrade/strategy_runtime/strategy_runtime_control.h")

if(BUILD_TESTING)
    add_executable(hepta_bounded_runtime_composition_tests
        "${CMAKE_SOURCE_DIR}/tests/bounded_runtime_composition_tests.cpp")
    target_include_directories(hepta_bounded_runtime_composition_tests PRIVATE
        "${CMAKE_SOURCE_DIR}/HeptaTrade")
    target_link_libraries(hepta_bounded_runtime_composition_tests PRIVATE
        hepta_feature_runtime
        hepta_management_control
        hepta_multi_agent_simulator
        hepta_strategy_runtime)
    set_target_properties(hepta_bounded_runtime_composition_tests PROPERTIES
        CXX_STANDARD 17
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    if(MSVC)
        target_compile_options(hepta_bounded_runtime_composition_tests PRIVATE
            /W4 /WX /UNDEBUG)
    else()
        target_compile_options(hepta_bounded_runtime_composition_tests PRIVATE
            -Wall -Wextra -Werror -UNDEBUG)
    endif()
    add_test(NAME hepta_bounded_runtime_composition_tests
             COMMAND hepta_bounded_runtime_composition_tests)
    set_tests_properties(hepta_bounded_runtime_composition_tests PROPERTIES
        LABELS "core;bounded-runtime-composition"
        TIMEOUT 30)
    if(TARGET hepta_core_test_binaries)
        add_dependencies(hepta_core_test_binaries
            hepta_bounded_runtime_composition_tests)
    endif()
endif()
