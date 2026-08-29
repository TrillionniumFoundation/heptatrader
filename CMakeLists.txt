# CMakeList.txt: 顶层 CMake 项目文件，在此处执行全局配置
# 并包含子项目。
#
cmake_minimum_required (VERSION 3.8)

project ("HeptaTrader")

option(BUILD_IB_PROBE "Build IB/TWS connectivity probe" OFF)
option(HEPTA_ENABLE_CONTAINERIZED_SYSTEMD_REHEARSAL
       "Register the explicit disposable Docker effective-systemd rehearsal"
       OFF)
option(HEPTA_ENABLE_AGENT_OS_USER_SYSTEMD_LIFECYCLE_GATE
       "Register the opt-in isolated user-systemd Gateway lifecycle gate"
       OFF)
option(HEPTA_ENABLE_AGENT_OS_ROOTFUL_SYSTEMD_E2E_GATE
       "Register the opt-in disposable four-UID Agent OS systemd E2E gate"
       OFF)
option(HEPTA_ENABLE_BROKER_NETWORK_ROOTFUL_GATE
       "Register the opt-in disposable broker lifecycle rootful gate"
       OFF)
option(HEPTA_ENABLE_PAPER_DOMAIN_ROOTFUL_SYSTEMD_GATE
       "Register the opt-in disposable PAPER-domain effective-systemd gate"
       OFF)
option(HEPTA_ENABLE_P1_DUAL_DOMAIN_ROOTFUL_REHEARSAL
       "Register the opt-in non-certifying P1 WATCH/PAPER dual-domain rehearsal"
       OFF)
option(HEPTA_ENABLE_P1_CAMPAIGN_ROOTFUL_LIVENESS_REHEARSAL
       "Register the opt-in non-certifying P1 campaign liveness rehearsal"
       OFF)
option(HEPTA_ENABLE_OPENCLAW_REAL_LOADER_GATE
       "Register the opt-in isolated OpenClaw real plugin-loader gate"
       OFF)
option(HEPTA_BUILD_LEGACY_MONOLITH
       "Build the deprecated multi-venue HeptaTrader composition binary"
       OFF)
option(HEPTA_BUILD_LEGACY_SIMULATOR
       "Build the deprecated Pegasus simulator requiring local prebuilt overlays"
       OFF)
set(HEPTA_SOAK_PROFILE "pr-smoke" CACHE STRING
    "Execution-gateway soak profile used by the parameterized CTest gate")
set_property(CACHE HEPTA_SOAK_PROFILE PROPERTY STRINGS
    pr-smoke release nightly)
if(NOT HEPTA_SOAK_PROFILE STREQUAL "pr-smoke" AND
   NOT HEPTA_SOAK_PROFILE STREQUAL "release" AND
   NOT HEPTA_SOAK_PROFILE STREQUAL "nightly")
  message(FATAL_ERROR
    "HEPTA_SOAK_PROFILE must be one of pr-smoke, release, or nightly")
endif()
set(HEPTA_REHEARSAL_IB_DISABLED_BUILD_DIR "" CACHE PATH
    "Fresh Release IB-disabled build used only for the rehearsal adapter stub")
set(HEPTA_REHEARSAL_SYSTEMD_BASE_IMAGE "" CACHE STRING
    "Preloaded Docker base image pinned as name@sha256:digest")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_IMAGE "" CACHE STRING
    "Preloaded reviewed Agent OS systemd base pinned as name@sha256:digest")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE "" CACHE FILEPATH
    "External reviewed GO provenance JSON for the Agent OS systemd base")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256 "" CACHE STRING
    "Canonical sha256 digest binding the reviewed Agent OS base provenance")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDKIT_IMAGE "" CACHE STRING
    "Preloaded reviewed BuildKit image pinned as name@sha256:digest")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE "" CACHE FILEPATH
    "External reviewed GO provenance for the isolated BuildKit/buildx toolchain")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256 "" CACHE STRING
    "Canonical sha256 digest binding the isolated builder provenance")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDX_BINARY_SHA256 "" CACHE STRING
    "Canonical sha256 digest binding the Docker-selected buildx plugin")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE "" CACHE FILEPATH
    "External reviewed GO provenance JSON for the exact AppArmor policy")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256 "" CACHE STRING
    "Canonical sha256 digest binding the reviewed AppArmor provenance")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE ""
    CACHE FILEPATH
    "External GO binding the Docker daemon to the AppArmor namespace")
set(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256 ""
    CACHE STRING
    "Canonical sha256 binding the Docker/AppArmor namespace provenance")
set(HEPTA_BROKER_NETWORK_ROOTFUL_BASE_IMAGE "" CACHE STRING
    "Preloaded broker-network gate base pinned as name@sha256:digest")
set(HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_IMAGE "" CACHE STRING
    "Preloaded PAPER-domain systemd gate base pinned as name@sha256:digest")
set(HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_IMAGE "" CACHE STRING
    "Preloaded P1 dual-domain rehearsal base pinned as name@sha256:digest")
set(HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT "" CACHE STRING
    "Exact source commit consumed by the non-certifying dual-domain rehearsal")
set(HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_IMAGE "" CACHE STRING
    "Preloaded P1 campaign liveness rehearsal base pinned as name@sha256:digest")
set(HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT "" CACHE STRING
    "Exact source commit consumed by the non-certifying campaign liveness rehearsal")

set(HEPTA_AGENT_OS_SOURCE_ONLY OFF)
set(HEPTA_AGENT_OS_SOURCE_MARKER
    "${CMAKE_SOURCE_DIR}/.hepta/agent-os-source-manifest.json")
if(NOT EXISTS "${CMAKE_SOURCE_DIR}/.git" AND
   EXISTS "${HEPTA_AGENT_OS_SOURCE_MARKER}")
  file(READ "${HEPTA_AGENT_OS_SOURCE_MARKER}"
       HEPTA_AGENT_OS_SOURCE_MARKER_CONTENT LIMIT 1048576)
  foreach(HEPTA_AGENT_OS_SOURCE_MARKER_FIELD IN ITEMS
      "\"schema\": \"hepta.agent-os-source-bundle.v1\""
      "\"bundle_class\": \"agent-os-source-only\""
      "\"paper_authorized\": false"
      "\"live_authorized\": false")
    string(FIND "${HEPTA_AGENT_OS_SOURCE_MARKER_CONTENT}"
           "${HEPTA_AGENT_OS_SOURCE_MARKER_FIELD}"
           HEPTA_AGENT_OS_SOURCE_MARKER_FIELD_POSITION)
    if(HEPTA_AGENT_OS_SOURCE_MARKER_FIELD_POSITION EQUAL -1)
      message(FATAL_ERROR
        "Agent OS source marker is malformed or weakens a safety boundary")
    endif()
  endforeach()
  set(HEPTA_AGENT_OS_SOURCE_ONLY ON)
  message(STATUS
    "Agent-OS-only source profile: repository/evidence tests are external")
endif()

include(CTest)

if(HEPTA_ENABLE_CONTAINERIZED_SYSTEMD_REHEARSAL)
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR
      "containerized systemd rehearsal requires a Linux build")
  endif()
  if(NOT BUILD_TESTING)
    message(FATAL_ERROR
      "containerized systemd rehearsal requires BUILD_TESTING=ON")
  endif()
  if(NOT CMAKE_EXPORT_COMPILE_COMMANDS)
    message(FATAL_ERROR
      "containerized systemd rehearsal requires CMAKE_EXPORT_COMPILE_COMMANDS=ON")
  endif()
endif()

if(HEPTA_ENABLE_AGENT_OS_USER_SYSTEMD_LIFECYCLE_GATE OR
   HEPTA_ENABLE_AGENT_OS_ROOTFUL_SYSTEMD_E2E_GATE OR
   HEPTA_ENABLE_BROKER_NETWORK_ROOTFUL_GATE OR
   HEPTA_ENABLE_PAPER_DOMAIN_ROOTFUL_SYSTEMD_GATE OR
   HEPTA_ENABLE_P1_DUAL_DOMAIN_ROOTFUL_REHEARSAL OR
   HEPTA_ENABLE_P1_CAMPAIGN_ROOTFUL_LIVENESS_REHEARSAL OR
   HEPTA_ENABLE_OPENCLAW_REAL_LOADER_GATE)
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR
      "Agent OS user-systemd/OpenClaw real-loader gates require Linux")
  endif()
  if(NOT BUILD_TESTING)
    message(FATAL_ERROR
      "Agent OS user-systemd/OpenClaw real-loader gates require BUILD_TESTING=ON")
  endif()
endif()

if(HEPTA_ENABLE_P1_CAMPAIGN_ROOTFUL_LIVENESS_REHEARSAL)
  if(NOT HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_IMAGE MATCHES
         "^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_IMAGE must be digest-pinned")
  endif()
  string(REGEX REPLACE "^.*@sha256:" ""
         HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_DIGEST
         "${HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_IMAGE}")
  string(LENGTH "${HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_DIGEST}"
         HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_DIGEST_LENGTH)
  if(NOT HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_BASE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "P1 campaign rootful liveness base digest must be 64 hex characters")
  endif()
  if(NOT HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT MATCHES
         "^[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT must be lowercase hex")
  endif()
  string(LENGTH
         "${HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT}"
         HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT_LENGTH)
  if(NOT HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_EXPECTED_SOURCE_COMMIT_LENGTH
         EQUAL 40)
    message(FATAL_ERROR
      "P1 campaign rootful liveness expected source commit must be 40 hex characters")
  endif()
endif()

if(HEPTA_ENABLE_P1_DUAL_DOMAIN_ROOTFUL_REHEARSAL)
  if(NOT HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_IMAGE MATCHES
         "^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_IMAGE must be digest-pinned")
  endif()
  string(REGEX REPLACE "^.*@sha256:" ""
         HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_DIGEST
         "${HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_IMAGE}")
  string(LENGTH "${HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_DIGEST}"
         HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_DIGEST_LENGTH)
  if(NOT HEPTA_P1_DUAL_DOMAIN_ROOTFUL_BASE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "P1 dual-domain rootful base digest must be 64 hex characters")
  endif()
  if(NOT HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT MATCHES
         "^[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT must be lowercase hex")
  endif()
  string(LENGTH "${HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT}"
         HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT_LENGTH)
  if(NOT HEPTA_P1_DUAL_DOMAIN_EXPECTED_SOURCE_COMMIT_LENGTH EQUAL 40)
    message(FATAL_ERROR
      "P1 dual-domain expected source commit must be 40 hex characters")
  endif()
endif()

if(HEPTA_ENABLE_BROKER_NETWORK_ROOTFUL_GATE)
  if(NOT HEPTA_BROKER_NETWORK_ROOTFUL_BASE_IMAGE MATCHES
         "^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_BROKER_NETWORK_ROOTFUL_BASE_IMAGE must be digest-pinned")
  endif()
  string(REGEX REPLACE "^.*@sha256:" "" HEPTA_BROKER_NETWORK_BASE_DIGEST
         "${HEPTA_BROKER_NETWORK_ROOTFUL_BASE_IMAGE}")
  string(LENGTH "${HEPTA_BROKER_NETWORK_BASE_DIGEST}"
         HEPTA_BROKER_NETWORK_BASE_DIGEST_LENGTH)
  if(NOT HEPTA_BROKER_NETWORK_BASE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "broker-network rootful base digest must be 64 hex characters")
  endif()
endif()

if(HEPTA_ENABLE_PAPER_DOMAIN_ROOTFUL_SYSTEMD_GATE)
  if(NOT HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_IMAGE MATCHES
         "^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_IMAGE must be digest-pinned")
  endif()
  string(REGEX REPLACE "^.*@sha256:" ""
         HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_DIGEST
         "${HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_IMAGE}")
  string(LENGTH "${HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_DIGEST}"
         HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_DIGEST_LENGTH)
  if(NOT HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_BASE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "PAPER-domain rootful systemd base digest must be 64 hex characters")
  endif()
endif()

if(HEPTA_ENABLE_AGENT_OS_ROOTFUL_SYSTEMD_E2E_GATE)
  if(NOT CMAKE_EXPORT_COMPILE_COMMANDS)
    message(FATAL_ERROR
      "Agent OS rootful systemd E2E gate requires CMAKE_EXPORT_COMPILE_COMMANDS=ON")
  endif()
  if(HEPTA_ENABLE_IBAPI)
    message(FATAL_ERROR
      "Agent OS rootful systemd E2E gate requires HEPTA_ENABLE_IBAPI=OFF")
  endif()
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_IMAGE MATCHES
         "^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_IMAGE must be digest-pinned")
  endif()
  string(REGEX REPLACE "^.*@sha256:" "" HEPTA_AGENT_OS_ROOTFUL_BASE_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_IMAGE}")
  string(LENGTH "${HEPTA_AGENT_OS_ROOTFUL_BASE_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_BASE_DIGEST_LENGTH)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_BASE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "Agent OS rootful systemd base digest must be 64 hex characters")
  endif()
  if(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE STREQUAL "" OR
     HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256 STREQUAL "")
    message(FATAL_ERROR
      "Agent OS rootful systemd gate requires both reviewed base provenance inputs")
  endif()
  if(NOT IS_ABSOLUTE
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS rootful systemd base provenance path must be absolute")
  endif()
  if(NOT EXISTS
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE}" OR
     IS_DIRECTORY
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE}" OR
     IS_SYMLINK
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS rootful systemd base provenance must be a regular non-symlink file")
  endif()
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256 MATCHES
         "^sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "Agent OS rootful systemd base provenance digest must be canonical sha256")
  endif()
  string(REGEX REPLACE "^sha256:" ""
         HEPTA_AGENT_OS_ROOTFUL_BASE_PROVENANCE_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256}")
  string(LENGTH "${HEPTA_AGENT_OS_ROOTFUL_BASE_PROVENANCE_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_BASE_PROVENANCE_DIGEST_LENGTH)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_BASE_PROVENANCE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "Agent OS rootful systemd base provenance digest must be 64 hex characters")
  endif()
  file(SHA256 "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE}"
       HEPTA_AGENT_OS_ROOTFUL_BASE_PROVENANCE_OBSERVED_SHA256)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE_SHA256 STREQUAL
         "sha256:${HEPTA_AGENT_OS_ROOTFUL_BASE_PROVENANCE_OBSERVED_SHA256}")
    message(FATAL_ERROR
      "Agent OS rootful systemd base provenance digest does not match its file")
  endif()
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDKIT_IMAGE MATCHES
         "^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "Agent OS rootful systemd BuildKit image must be digest-pinned")
  endif()
  string(REGEX REPLACE "^.*@sha256:" ""
         HEPTA_AGENT_OS_ROOTFUL_BUILDKIT_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDKIT_IMAGE}")
  string(LENGTH "${HEPTA_AGENT_OS_ROOTFUL_BUILDKIT_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_BUILDKIT_DIGEST_LENGTH)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_BUILDKIT_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "Agent OS rootful systemd BuildKit digest must be 64 hex characters")
  endif()
  if(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE STREQUAL "" OR
     HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256 STREQUAL "")
    message(FATAL_ERROR
      "Agent OS rootful systemd gate requires both isolated builder provenance inputs")
  endif()
  if(NOT IS_ABSOLUTE
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS isolated builder provenance path must be absolute")
  endif()
  if(NOT EXISTS
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}" OR
     IS_DIRECTORY
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}" OR
     IS_SYMLINK
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS isolated builder provenance must be a regular non-symlink file")
  endif()
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256 MATCHES
         "^sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "Agent OS isolated builder provenance digest must be canonical sha256")
  endif()
  string(REGEX REPLACE "^sha256:" ""
         HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256}")
  string(LENGTH "${HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_DIGEST_LENGTH)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "Agent OS isolated builder provenance digest must be 64 hex characters")
  endif()
  file(SIZE "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}"
       HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_SIZE)
  if(HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_SIZE LESS 1 OR
     HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_SIZE GREATER 16384)
    message(FATAL_ERROR
      "Agent OS isolated builder provenance size is outside the reviewed bound")
  endif()
  file(SHA256 "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE}"
       HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_OBSERVED_SHA256)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE_SHA256 STREQUAL
         "sha256:${HEPTA_AGENT_OS_ROOTFUL_BUILDER_PROVENANCE_OBSERVED_SHA256}")
    message(FATAL_ERROR
      "Agent OS isolated builder provenance digest does not match its file")
  endif()
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDX_BINARY_SHA256 MATCHES
         "^sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "Agent OS buildx binary digest must be canonical sha256")
  endif()
  string(REGEX REPLACE "^sha256:" ""
         HEPTA_AGENT_OS_ROOTFUL_BUILDX_BINARY_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDX_BINARY_SHA256}")
  string(LENGTH "${HEPTA_AGENT_OS_ROOTFUL_BUILDX_BINARY_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_BUILDX_BINARY_DIGEST_LENGTH)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_BUILDX_BINARY_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "Agent OS buildx binary digest must be 64 hex characters")
  endif()
  if(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE STREQUAL "" OR
     HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256 STREQUAL "")
    message(FATAL_ERROR
      "Agent OS rootful systemd gate requires both AppArmor provenance inputs")
  endif()
  if(NOT IS_ABSOLUTE
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS rootful systemd AppArmor provenance path must be absolute")
  endif()
  if(NOT EXISTS
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE}" OR
     IS_DIRECTORY
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE}" OR
     IS_SYMLINK
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS rootful systemd AppArmor provenance must be a regular non-symlink file")
  endif()
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256 MATCHES
         "^sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "Agent OS rootful systemd AppArmor provenance digest must be canonical sha256")
  endif()
  string(REGEX REPLACE "^sha256:" ""
         HEPTA_AGENT_OS_ROOTFUL_APPARMOR_PROVENANCE_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256}")
  string(LENGTH "${HEPTA_AGENT_OS_ROOTFUL_APPARMOR_PROVENANCE_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_APPARMOR_PROVENANCE_DIGEST_LENGTH)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_APPARMOR_PROVENANCE_DIGEST_LENGTH EQUAL 64)
    message(FATAL_ERROR
      "Agent OS rootful systemd AppArmor provenance digest must be 64 hex characters")
  endif()
  file(SHA256 "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE}"
       HEPTA_AGENT_OS_ROOTFUL_APPARMOR_PROVENANCE_OBSERVED_SHA256)
  if(NOT HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE_SHA256 STREQUAL
         "sha256:${HEPTA_AGENT_OS_ROOTFUL_APPARMOR_PROVENANCE_OBSERVED_SHA256}")
    message(FATAL_ERROR
      "Agent OS rootful systemd AppArmor provenance digest does not match its file")
  endif()
  if(HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE
       STREQUAL "" OR
     HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256
       STREQUAL "")
    message(FATAL_ERROR
      "Agent OS rootful systemd gate requires both Docker/AppArmor namespace provenance inputs")
  endif()
  if(NOT IS_ABSOLUTE
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS Docker/AppArmor namespace provenance path must be absolute")
  endif()
  if(NOT EXISTS
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE}" OR
     IS_DIRECTORY
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE}" OR
     IS_SYMLINK
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE}")
    message(FATAL_ERROR
      "Agent OS Docker/AppArmor namespace provenance must be a regular non-symlink file")
  endif()
  if(NOT
     HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256
       MATCHES "^sha256:[0-9a-f]+$")
    message(FATAL_ERROR
      "Agent OS Docker/AppArmor namespace provenance digest must be canonical sha256")
  endif()
  string(REGEX REPLACE "^sha256:" ""
         HEPTA_AGENT_OS_ROOTFUL_DOCKER_AA_NAMESPACE_PROVENANCE_DIGEST
         "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256}")
  string(LENGTH
         "${HEPTA_AGENT_OS_ROOTFUL_DOCKER_AA_NAMESPACE_PROVENANCE_DIGEST}"
         HEPTA_AGENT_OS_ROOTFUL_DOCKER_AA_NAMESPACE_PROVENANCE_DIGEST_LENGTH)
  if(NOT
     HEPTA_AGENT_OS_ROOTFUL_DOCKER_AA_NAMESPACE_PROVENANCE_DIGEST_LENGTH
       EQUAL 64)
    message(FATAL_ERROR
      "Agent OS Docker/AppArmor namespace provenance digest must be 64 hex characters")
  endif()
  file(SHA256
       "${HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE}"
       HEPTA_AGENT_OS_ROOTFUL_DOCKER_AA_NAMESPACE_PROVENANCE_OBSERVED_SHA256)
  if(NOT
     HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE_SHA256
       STREQUAL
       "sha256:${HEPTA_AGENT_OS_ROOTFUL_DOCKER_AA_NAMESPACE_PROVENANCE_OBSERVED_SHA256}")
    message(FATAL_ERROR
      "Agent OS Docker/AppArmor namespace provenance digest does not match its file")
  endif()
endif()

# 包含子项目。
if(HEPTA_BUILD_LEGACY_MONOLITH)
  add_subdirectory ("HeptaStrategy")
endif()
add_subdirectory ("HeptaTrade")
if(HEPTA_BUILD_LEGACY_SIMULATOR)
  add_subdirectory ("HeptaSimulator")
endif()

if(BUILD_IB_PROBE)
  add_subdirectory("docs/ib_probe")
endif()

if(BUILD_TESTING)
  add_subdirectory("tests")
endif()
