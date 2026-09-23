cmake_minimum_required(VERSION 3.16)
project(ib_connection_probe CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The root build already owns the pinned IB SDK source list, Decimal ABI probe,
# and Intel BID linkage through hepta_ibapi_client.  Reuse that target instead
# of maintaining a second SDK file inventory here; the old probe list referenced
# EPosixClientSocket.cpp, which is absent from the reviewed SDK snapshot.
if(NOT TARGET hepta_ibapi_client)
  message(FATAL_ERROR
    "BUILD_IB_PROBE=ON requires HEPTA_ENABLE_IBAPI=ON with the same pinned "
    "IBAPI_ROOT and IBAPI_DECIMAL_LIBRARY used by the execution candidate")
endif()

add_executable(ib_connection_probe
  ${CMAKE_CURRENT_LIST_DIR}/../ib-connection-probe.cpp)
target_link_libraries(ib_connection_probe PRIVATE hepta_ibapi_client)
target_compile_features(ib_connection_probe PRIVATE cxx_std_17)

message(STATUS "[ib_probe] reusing canonical hepta_ibapi_client")
