# Additive, SDK-free integration. Included after existing test targets so the
# canonical core umbrella builds and runs these tests without a parallel lane.
add_library(hepta_research_core STATIC
    ${CMAKE_SOURCE_DIR}/strategies/native_research/market.cpp
    ${CMAKE_SOURCE_DIR}/strategies/native_research/replay.cpp
    ${CMAKE_SOURCE_DIR}/strategies/native_research/signal.cpp)
target_include_directories(hepta_research_core PUBLIC ${CMAKE_SOURCE_DIR}/strategies/native_research)
target_compile_features(hepta_research_core PUBLIC cxx_std_11)
set_target_properties(hepta_research_core PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)

add_library(hepta_strategy_intent_client STATIC
    ${CMAKE_SOURCE_DIR}/HeptaTrade/client/strategy_intent_client.cpp)
target_include_directories(hepta_strategy_intent_client PUBLIC ${CMAKE_SOURCE_DIR}/HeptaTrade)
target_link_libraries(hepta_strategy_intent_client PUBLIC hepta_native_tool_client hepta_research_core)
target_compile_features(hepta_strategy_intent_client PUBLIC cxx_std_11)
set_target_properties(hepta_strategy_intent_client PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)

if(BUILD_TESTING)
    add_executable(hepta_native_research_tests ${CMAKE_SOURCE_DIR}/tests/native_research_tests.cpp)
    target_link_libraries(hepta_native_research_tests PRIVATE hepta_research_core)
    add_executable(hepta_native_strategy_gateway_tests ${CMAKE_SOURCE_DIR}/tests/native_strategy_gateway_tests.cpp)
    target_link_libraries(hepta_native_strategy_gateway_tests PRIVATE
        hepta_strategy_intent_client hepta_agent_os_core hepta_execution_core hepta_simulator_runtime)
    add_executable(hepta_native_strategy_client_link_tests ${CMAKE_SOURCE_DIR}/tests/native_strategy_client_link_tests.cpp)
    target_link_libraries(hepta_native_strategy_client_link_tests PRIVATE hepta_strategy_intent_client)
    foreach(t hepta_native_research_tests hepta_native_strategy_gateway_tests hepta_native_strategy_client_link_tests)
        set_target_properties(${t} PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
        add_test(NAME ${t} COMMAND ${t})
        set_tests_properties(${t} PROPERTIES LABELS "core;research-integration" TIMEOUT 120)
        add_dependencies(hepta_core_test_binaries ${t})
    endforeach()
endif()
