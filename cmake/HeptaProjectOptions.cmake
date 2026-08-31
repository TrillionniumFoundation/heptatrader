set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(HEPTA_ENABLE_HARDENING
       "Enable compiler and linker hardening on runtime targets" ON)
option(HEPTA_ENABLE_ASAN_UBSAN
       "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(HEPTA_ENABLE_TSAN
       "Build with ThreadSanitizer" OFF)
option(HEPTA_WARNINGS_AS_ERRORS
       "Treat compiler warnings as errors" OFF)

if(HEPTA_ENABLE_ASAN_UBSAN AND HEPTA_ENABLE_TSAN)
    message(FATAL_ERROR
        "HEPTA_ENABLE_ASAN_UBSAN and HEPTA_ENABLE_TSAN are mutually exclusive")
endif()

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(-Wall -Wextra -Wpedantic -Wformat=2)
    if(HEPTA_WARNINGS_AS_ERRORS)
        add_compile_options(-Werror)
    endif()

    if(HEPTA_ENABLE_ASAN_UBSAN)
        add_compile_options(
            -fsanitize=address,undefined
            -fno-omit-frame-pointer
            -fno-sanitize-recover=all)
        add_link_options(
            -fsanitize=address,undefined
            -fno-omit-frame-pointer
            -fno-sanitize-recover=all)
    elseif(HEPTA_ENABLE_TSAN)
        add_compile_options(-fsanitize=thread -fno-omit-frame-pointer)
        add_link_options(-fsanitize=thread -fno-omit-frame-pointer)
    endif()
endif()
