cmake_minimum_required(VERSION 3.16)
project(ib_connection_probe CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED IBAPI_ROOT)
  message(FATAL_ERROR "BUILD_IB_PROBE=ON requires -DIBAPI_ROOT=<path to IB C++ API root, e.g. E:/trading/TWS API/source/cppclient>")
endif()

set(IBAPI_INCLUDE_DIR "${IBAPI_ROOT}")
set(IBAPI_SRC_DIR "${IBAPI_ROOT}")

add_executable(ib_connection_probe
  ${CMAKE_CURRENT_LIST_DIR}/../ib-connection-probe.cpp
  ${IBAPI_SRC_DIR}/EClient.cpp
  ${IBAPI_SRC_DIR}/EClientSocket.cpp
  ${IBAPI_SRC_DIR}/EPosixClientSocket.cpp
  ${IBAPI_SRC_DIR}/EReader.cpp
  ${IBAPI_SRC_DIR}/EWrapper.cpp
  ${IBAPI_SRC_DIR}/Order.cpp
  ${IBAPI_SRC_DIR}/Contract.cpp
  ${IBAPI_SRC_DIR}/Execution.cpp
  ${IBAPI_SRC_DIR}/CommissionReport.cpp
)

target_include_directories(ib_connection_probe PRIVATE ${IBAPI_INCLUDE_DIR})

if(WIN32)
  target_compile_definitions(ib_connection_probe PRIVATE _WIN32_WINNT=0x0601)
  target_link_libraries(ib_connection_probe PRIVATE ws2_32)
endif()

message(STATUS "[ib_probe] IBAPI_ROOT=${IBAPI_ROOT}")
