# Selected HeptaDLL-domain capabilities, not the retired legacy runtime.
# Pure research never links the Gateway, Execution authority or a vendor SDK.
add_library(hepta_research_market_data STATIC
    strategies/native/market_data.cpp)
add_library(hepta_research_replay STATIC
    strategies/native/replay.cpp)
add_library(hepta_research_strategy STATIC
    strategies/native/strategy.cpp)
target_link_libraries(hepta_research_replay PUBLIC hepta_research_market_data)
target_link_libraries(hepta_research_strategy PUBLIC hepta_research_market_data)
add_library(hepta_research_client STATIC
    HeptaTrade/client/research_intent_client.cpp)
target_link_libraries(hepta_research_client PUBLIC
    hepta_research_strategy hepta_native_tool_client)
add_executable(hepta_research strategies/native/research_cli.cpp)
set_target_properties(hepta_research PROPERTIES OUTPUT_NAME hepta-research)
target_link_libraries(hepta_research PRIVATE
    hepta_research_replay hepta_research_strategy)
foreach(target hepta_research_market_data hepta_research_replay
               hepta_research_strategy hepta_research_client hepta_research)
    set_target_properties(${target} PROPERTIES CXX_STANDARD 11
        CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
    target_include_directories(${target} PUBLIC ${CMAKE_SOURCE_DIR})
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    endif()
endforeach()

# The native client currently depends on the maintained POSIX local protocol.
# No new service, SDK, capability or installed product is introduced here.
if(BUILD_TESTING)
    add_executable(hepta_research_core_tests tests/research_core_tests.cpp)
    target_link_libraries(hepta_research_core_tests PRIVATE
        hepta_research_replay hepta_research_strategy)
    add_executable(hepta_research_client_tests tests/research_intent_client_tests.cpp)
    target_link_libraries(hepta_research_client_tests PRIVATE
        hepta_research_client hepta_agent_os_core)
    foreach(target hepta_research_core_tests hepta_research_client_tests)
        set_target_properties(${target} PROPERTIES CXX_STANDARD 11
            CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
        add_test(NAME ${target} COMMAND ${target})
        set_tests_properties(${target} PROPERTIES LABELS "core;research" TIMEOUT 60)
        add_dependencies(hepta_core_test_binaries ${target})
    endforeach()
    add_dependencies(hepta_core_test_binaries hepta_research)
    add_test(NAME hepta_research_cli_bars COMMAND hepta_research bars TEST.FUT 60000
        ${CMAKE_SOURCE_DIR}/tests/fixtures/research/sessions.csv
        ${CMAKE_SOURCE_DIR}/tests/fixtures/research/ticks.csv)
    add_test(NAME hepta_research_cli_backtest COMMAND hepta_research backtest TEST.FUT 60000
        ${CMAKE_SOURCE_DIR}/tests/fixtures/research/sessions.csv
        ${CMAKE_SOURCE_DIR}/tests/fixtures/research/ticks.csv 1 2 2 1 10 1 0 10000)
    set_tests_properties(hepta_research_cli_bars hepta_research_cli_backtest
        PROPERTIES LABELS "core;research" TIMEOUT 30)
    set_tests_properties(hepta_research_cli_bars PROPERTIES
        PASS_REGULAR_EXPRESSION "TEST.FUT,20260922,301000,361000,100,101,100,101,2,false")
    set_tests_properties(hepta_research_cli_backtest PROPERTIES
        PASS_REGULAR_EXPRESSION "OFFLINE_RESEARCH example.ma orders=2 fills=1 position=-2")
endif()
