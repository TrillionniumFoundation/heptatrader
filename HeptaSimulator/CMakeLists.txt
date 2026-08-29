cmake_minimum_required (VERSION 3.8)

# set the project name
project (HeptaSimulator)

# ����Ƿ�Ϊ Ubuntu ϵͳ
execute_process(
    COMMAND lsb_release -is
    OUTPUT_VARIABLE LINUX_DISTRO
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# ����ϵͳ��ر���
if(LINUX_DISTRO STREQUAL "Ubuntu")
    set(IS_UBUNTU TRUE)
    message(STATUS "Detected Ubuntu system")
else()
    set(IS_UBUNTU FALSE)
    message(STATUS "Detected non-Ubuntu system: ${LINUX_DISTRO}")
endif()

# ���c++��������־������c++11֧�ֱ���
include(CheckCXXCompilerFlag)
CHECK_CXX_COMPILER_FLAG("-std=c++11" COMPILER_SUPPORTS_CXX11)
CHECK_CXX_COMPILER_FLAG("-std=c++0x" COMPILER_SUPPORTS_CXX0X)

if(COMPILER_SUPPORTS_CXX11)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++11")
elseif(COMPILER_SUPPORTS_CXX0X)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++0x")
else()
message(STATUS "The compiler ${CMAKE_CXX_COMPILER} has no C++11 support. Please use a different C++ compiler.")
endif()

#set(CMAKE_EXE_LINKER_FLAGS "-no-pie")

set(CMAKE_CXX_FLAGS_DEBUG "$ENV{CXXFLAGS} -O0 -Wall -g -ggdb")  # Debugģʽ�µı���ָ��
set(CMAKE_CXX_FLAGS_RELEASE "$ENV{CXXFLAGS} -O3 -Wall")         # Releaseģʽ�µı���ָ��

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_DEBUG   ${PROJECT_SOURCE_DIR}/../bin/Debug/)    
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE  ${PROJECT_SOURCE_DIR}/../bin/Release/) 

add_definitions(-std=c++11)
add_definitions(-D__LINUX__=1 -Dlinux)


include_directories ("../HeptaStrategy/")
include_directories ("../Interface/include/")
include_directories ("../Interface/")

string(TOUPPER "${CMAKE_BUILD_TYPE}" HEPTA_SIM_BUILD_TYPE_UPPER)

if (HEPTA_SIM_BUILD_TYPE_UPPER STREQUAL "DEBUG")
    add_definitions(-g -DDEBUG)
    if(IS_UBUNTU)
        link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Ubuntu/Debug/")
    else()
        link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Linux/Debug/")
    endif()
    link_directories("${PROJECT_SOURCE_DIR}/../bin/Debug/")
elseif (HEPTA_SIM_BUILD_TYPE_UPPER STREQUAL "RELEASE")
    add_definitions(-DNDEBUG -O3)
    if(IS_UBUNTU)
        link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Ubuntu/Release/")
    else()
        link_directories("${PROJECT_SOURCE_DIR}/../Interface/lib/Linux/Release/")
    endif()
    link_directories("${PROJECT_SOURCE_DIR}/../bin/Release/")
endif()

# ��ȡ���ڴ�����CMakeLists.txt���ڵ��ļ������������Դ�ļ����ŵ�DIR_SRC��������
aux_source_directory(${CMAKE_CURRENT_LIST_DIR} DIR_SRC)

# add the executable
add_executable(HeptaSimulator ${DIR_SRC})

target_link_libraries(HeptaSimulator PRIVATE pthread libHeptaStrategy.a libheptaHeptaDLL_Linux.a libTinyXml_Linux.a)

# Link OpenMP runtime for libheptaHeptaDLL_Linux symbols (e.g. omp_set_num_threads)
find_package(OpenMP)
if (OpenMP_CXX_FOUND)
    target_link_libraries(HeptaSimulator PRIVATE OpenMP::OpenMP_CXX)
elseif(UNIX)
    target_link_libraries(HeptaSimulator PRIVATE gomp)
endif()
