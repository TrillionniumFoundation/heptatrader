cmake_minimum_required(VERSION 3.16)
set(HEPTA_RESEARCH_STANDALONE OFF)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    set(HEPTA_RESEARCH_STANDALONE ON)
    project(HeptaResearch LANGUAGES CXX)
    include(CTest)
endif()
include(GNUInstallDirs)
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
add_library(hepta_research_data STATIC src/market_data.cpp)
add_library(Hepta::ResearchData ALIAS hepta_research_data)
target_include_directories(hepta_research_data PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_compile_features(hepta_research_data PUBLIC cxx_std_11)
set_target_properties(hepta_research_data PROPERTIES CXX_EXTENSIONS OFF
    POSITION_INDEPENDENT_CODE ON EXPORT_NAME ResearchData)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(hepta_research_data PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
add_executable(hepta_research_bars src/bars_main.cpp)
set_target_properties(hepta_research_bars PROPERTIES
    OUTPUT_NAME hepta-research-bars EXPORT_NAME ResearchBars)
target_link_libraries(hepta_research_bars PRIVATE Hepta::ResearchData)
if(BUILD_TESTING)
    add_executable(hepta_research_data_tests ../tests/research/market_data_tests.cpp)
    target_link_libraries(hepta_research_data_tests PRIVATE Hepta::ResearchData)
    add_test(NAME hepta_research_data_tests COMMAND hepta_research_data_tests)
    find_package(Python3 3.10 COMPONENTS Interpreter REQUIRED)
    add_test(NAME hepta_research_python_tests
        COMMAND ${Python3_EXECUTABLE} -B -m unittest discover -s ${CMAKE_CURRENT_SOURCE_DIR}/../tests/research -p test_*.py)
    set_tests_properties(hepta_research_data_tests hepta_research_python_tests PROPERTIES LABELS "core;research")
    set_tests_properties(hepta_research_python_tests PROPERTIES
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/python;HEPTA_RESEARCH_BARS=$<TARGET_FILE:hepta_research_bars>")
    if(TARGET hepta_core_test_binaries)
        add_dependencies(hepta_core_test_binaries hepta_research_bars hepta_research_data_tests)
    endif()
endif()
# A separate research SDK prefix only. Root runtime install/package ownership
# remains unchanged; the Gateway and Execution never acquire these imports.
if(HEPTA_RESEARCH_STANDALONE)
    foreach(dir CMAKE_INSTALL_BINDIR CMAKE_INSTALL_LIBDIR CMAKE_INSTALL_INCLUDEDIR CMAKE_INSTALL_DATADIR)
        if(IS_ABSOLUTE "${${dir}}")
            message(FATAL_ERROR "Relocatable research SDK requires relative ${dir}")
        endif()
    endforeach()
    include(CMakePackageConfigHelpers)
    set(_hepta_research_config_dir "${CMAKE_INSTALL_LIBDIR}/cmake/HeptaResearch")
    configure_package_config_file(cmake/HeptaResearchConfig.cmake.in
        "${CMAKE_CURRENT_BINARY_DIR}/HeptaResearchConfig.cmake"
        INSTALL_DESTINATION "${_hepta_research_config_dir}")
    file(RELATIVE_PATH HEPTA_RESEARCH_PYTHON_RELATIVE
        "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_BINDIR}"
        "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/heptatrader/research/python")
    configure_file(cmake/replay_main.py.in
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-replay" @ONLY)
    configure_file(cmake/legacy_main.py.in
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-import" @ONLY)
    configure_file(cmake/ticks_main.py.in
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-ticks" @ONLY)
    configure_file(cmake/binary_main.py.in
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-binary" @ONLY)
    install(TARGETS hepta_research_data hepta_research_bars EXPORT HeptaResearchTargets
        ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
        RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
    install(DIRECTORY include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}" FILES_MATCHING PATTERN "*.hpp")
    install(EXPORT HeptaResearchTargets NAMESPACE Hepta:: DESTINATION "${_hepta_research_config_dir}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/HeptaResearchConfig.cmake"
        DESTINATION "${_hepta_research_config_dir}")
    install(DIRECTORY python/hepta_research
        DESTINATION "${CMAKE_INSTALL_DATADIR}/heptatrader/research/python"
        FILES_MATCHING PATTERN "*.py")
    install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-replay"
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-import"
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-ticks"
        "${CMAKE_CURRENT_BINARY_DIR}/hepta-research-binary"
        DESTINATION "${CMAKE_INSTALL_BINDIR}")
    install(FILES INSTALL.md LEGACY-IMPORT.md DATA-FORMATS.md BINARY-IMPORT.md DESTINATION "${CMAKE_INSTALL_DATADIR}/doc/hepta-research")
    if(BUILD_TESTING)
        add_test(NAME hepta_research_format_install_tests
            COMMAND ${Python3_EXECUTABLE} -B
                ${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/format_install_smoke.py
                --build-dir ${CMAKE_CURRENT_BINARY_DIR} --config $<CONFIG>
                --bindir ${CMAKE_INSTALL_BINDIR} --datadir ${CMAKE_INSTALL_DATADIR})
        set_tests_properties(hepta_research_format_install_tests PROPERTIES
            LABELS "research;install" TIMEOUT 180)
        add_test(NAME hepta_research_binary_install_tests
            COMMAND ${Python3_EXECUTABLE} -B
                ${CMAKE_CURRENT_SOURCE_DIR}/../tests/research/binary_install_smoke.py
                --build-dir ${CMAKE_CURRENT_BINARY_DIR} --config $<CONFIG>
                --bindir ${CMAKE_INSTALL_BINDIR} --datadir ${CMAKE_INSTALL_DATADIR})
        set_tests_properties(hepta_research_binary_install_tests PROPERTIES
            LABELS "research;install" TIMEOUT 180)
    endif()
endif()
