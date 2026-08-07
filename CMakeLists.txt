cmake_minimum_required(VERSION 3.25)

project(
  itchlab
  VERSION 0.1.0
  DESCRIPTION "Offline Nasdaq ITCH research platform"
  LANGUAGES CXX
)

option(ITCHLAB_WARNINGS_AS_ERRORS "Treat project compiler warnings as errors" ON)
option(ITCHLAB_ENABLE_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviourSanitizer" OFF)
option(ITCHLAB_ENABLE_COVERAGE "Enable source coverage instrumentation" OFF)

if(ITCHLAB_ENABLE_SANITIZERS AND ITCHLAB_ENABLE_COVERAGE)
  message(FATAL_ERROR "Sanitizer and coverage instrumentation cannot be enabled together")
endif()

include(FetchContent)
FetchContent_Declare(
  nlohmann_json
  GIT_REPOSITORY https://github.com/nlohmann/json.git
  GIT_TAG 55f93686c01528224f448c19128836e7df245f72
  GIT_SHALLOW FALSE
)
FetchContent_MakeAvailable(nlohmann_json)
find_package(ZLIB REQUIRED)

find_package(Git QUIET)

function(itchlab_configure_target target_name)
  target_compile_features(${target_name} PRIVATE cxx_std_20)
  set_target_properties(${target_name} PROPERTIES CXX_EXTENSIONS OFF)

  if(MSVC)
    target_compile_options(${target_name} PRIVATE /W4)
    if(ITCHLAB_WARNINGS_AS_ERRORS)
      target_compile_options(${target_name} PRIVATE /WX)
    endif()
  elseif(CMAKE_CXX_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
    target_compile_options(
      ${target_name}
      PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Wconversion
        -Wsign-conversion
    )
    if(ITCHLAB_WARNINGS_AS_ERRORS)
      target_compile_options(${target_name} PRIVATE -Werror)
    endif()
  endif()

  if(ITCHLAB_ENABLE_SANITIZERS)
    if(CMAKE_CXX_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
      target_compile_options(
        ${target_name}
        PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer
      )
      target_link_options(${target_name} PRIVATE -fsanitize=address,undefined)
    else()
      message(FATAL_ERROR "The sanitizers preset requires Clang, AppleClang or GCC")
    endif()
  endif()

  if(ITCHLAB_ENABLE_COVERAGE)
    if(CMAKE_CXX_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
      target_compile_options(${target_name} PRIVATE -O0 -g --coverage)
      target_link_options(${target_name} PRIVATE --coverage)
    else()
      message(FATAL_ERROR "The coverage preset requires Clang, AppleClang or GCC")
    endif()
  endif()
endfunction()

add_library(
  itchlab_core
  cpp/src/book/order_book.cpp
  cpp/src/config/canonical_json.cpp
  cpp/src/config/replay_config.cpp
  cpp/src/core/cancellation.cpp
  cpp/src/core/errors.cpp
  cpp/src/core/sha256.cpp
  cpp/src/input/byte_source.cpp
  cpp/src/input/file_source.cpp
  cpp/src/input/framed_reader.cpp
  cpp/src/input/gzip_source.cpp
  cpp/src/input/source_factory.cpp
  cpp/src/inspect/source_inspector.cpp
  cpp/src/itch/byte_decode.cpp
  cpp/src/itch/decoder.cpp
  cpp/src/output/binary_encode.cpp
  cpp/src/output/diagnostic_sinks.cpp
  cpp/src/output/event_writer.cpp
  cpp/src/output/manifest.cpp
  cpp/src/output/snapshot_writer.cpp
  cpp/src/replay/instrument_directory.cpp
  cpp/src/replay/error_policy.cpp
  cpp/src/replay/progress_reporter.cpp
  cpp/src/replay/replay_coordinator.cpp
  cpp/src/replay/session_state.cpp
  cpp/src/validation/validator.cpp
)
target_include_directories(itchlab_core PUBLIC cpp/include)
target_link_libraries(itchlab_core PRIVATE nlohmann_json::nlohmann_json ZLIB::ZLIB)
itchlab_configure_target(itchlab_core)

set(ITCHLAB_BUILD_METADATA_HEADER
    "${CMAKE_CURRENT_BINARY_DIR}/generated/itchlab/build_metadata.hpp"
)
add_custom_target(
  itchlab_build_metadata
  COMMAND
    "${CMAKE_COMMAND}"
    "-DITCHLAB_METADATA_OUTPUT=${ITCHLAB_BUILD_METADATA_HEADER}"
    "-DITCHLAB_METADATA_SOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DITCHLAB_METADATA_GIT=${GIT_EXECUTABLE}"
    "-DITCHLAB_METADATA_VERSION=${PROJECT_VERSION}"
    "-DITCHLAB_METADATA_COMPILER=${CMAKE_CXX_COMPILER_ID}"
    "-DITCHLAB_METADATA_COMPILER_VERSION=${CMAKE_CXX_COMPILER_VERSION}"
    "-DITCHLAB_METADATA_TARGET=${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}"
    "-DITCHLAB_METADATA_BUILD_TYPE=$<CONFIG>"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/GenerateBuildMetadata.cmake"
  BYPRODUCTS "${ITCHLAB_BUILD_METADATA_HEADER}"
  COMMENT "Refreshing exact build lineage metadata"
  VERBATIM
)

add_library(
  itchlab_cli
  cpp/apps/itchlab/cli.cpp
  cpp/apps/itchlab/signal_adapter.cpp
)
add_dependencies(itchlab_cli itchlab_build_metadata)
target_include_directories(itchlab_cli PUBLIC cpp/apps PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(itchlab_cli PUBLIC itchlab_core PRIVATE nlohmann_json::nlohmann_json)
itchlab_configure_target(itchlab_cli)

add_executable(itchlab cpp/apps/itchlab/main.cpp)
target_link_libraries(itchlab PRIVATE itchlab_cli)
itchlab_configure_target(itchlab)

include(CTest)
if(BUILD_TESTING)
  FetchContent_Declare(
    Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG 2b60af89e23d28eefc081bc930831ee9d45ea58b
    GIT_SHALLOW FALSE
  )
  FetchContent_MakeAvailable(Catch2)

  add_executable(
    itchlab_core_tests
    tests/cpp/contract/canonical_hash_test.cpp
    tests/cpp/contract/replay_config_test.cpp
    tests/cpp/integration/framed_reader_integration_test.cpp
    tests/cpp/integration/decoder_integration_test.cpp
    tests/cpp/integration/book_integration_test.cpp
    tests/cpp/integration/task007_cli_integration_test.cpp
    tests/cpp/integration/task007_replay_integration_test.cpp
    tests/cpp/integration/task008_e2e_integration_test.cpp
    tests/cpp/integration/task011_replay_integration_test.cpp
    tests/cpp/integration/task012_cancellation_e2e_test.cpp
    tests/cpp/integration/task012_replay_integration_test.cpp
    tests/cpp/integration/task013_event_writer_integration_test.cpp
    tests/cpp/integration/task014_publication_integration_test.cpp
    tests/cpp/integration/task015_validation_integration_test.cpp
    tests/cpp/property/order_book_property_test.cpp
    tests/cpp/property/framed_reader_property_test.cpp
    tests/cpp/unit/byte_decode_test.cpp
    tests/cpp/unit/byte_source_test.cpp
    tests/cpp/unit/binary_encode_test.cpp
    tests/cpp/unit/cancellation_test.cpp
    tests/cpp/unit/decoder_test.cpp
    tests/cpp/unit/errors_test.cpp
    tests/cpp/unit/error_policy_test.cpp
    tests/cpp/unit/event_writer_test.cpp
    tests/cpp/unit/framed_reader_test.cpp
    tests/cpp/unit/order_book_test.cpp
    tests/cpp/unit/progress_reporter_test.cpp
    tests/cpp/unit/instrument_directory_test.cpp
    tests/cpp/unit/manifest_test.cpp
    tests/cpp/unit/session_state_test.cpp
    tests/cpp/unit/sha256_test.cpp
    tests/cpp/unit/snapshot_writer_test.cpp
    tests/cpp/unit/types_test.cpp
    tests/cpp/unit/validator_test.cpp
  )
  target_compile_definitions(
    itchlab_core_tests
    PRIVATE
      ITCHLAB_BINARY_PATH="$<TARGET_FILE:itchlab>"
      ITCHLAB_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
  )
  target_link_libraries(
    itchlab_core_tests
    PRIVATE itchlab_cli itchlab_core Catch2::Catch2WithMain nlohmann_json::nlohmann_json
  )
  itchlab_configure_target(itchlab_core_tests)

  list(APPEND CMAKE_MODULE_PATH "${catch2_SOURCE_DIR}/extras")
  include(Catch)
  catch_discover_tests(itchlab_core_tests)

  add_test(NAME TASK_001_cpp_cli_version COMMAND itchlab --version)
  set_tests_properties(
    TASK_001_cpp_cli_version
    PROPERTIES PASS_REGULAR_EXPRESSION "^itchlab ${PROJECT_VERSION}"
  )

  add_test(NAME TASK_001_cpp_cli_help COMMAND itchlab --help)
  set_tests_properties(
    TASK_001_cpp_cli_help
    PROPERTIES PASS_REGULAR_EXPRESSION "Offline Nasdaq ITCH research platform"
  )

  add_test(NAME TASK_001_cpp_cli_rejects_unsupported_argument COMMAND itchlab inspect)
  set_tests_properties(
    TASK_001_cpp_cli_rejects_unsupported_argument
    PROPERTIES WILL_FAIL TRUE
  )

  add_test(NAME TASK_007_cpp_cli_inspect_help COMMAND itchlab inspect --help)
  set_tests_properties(
    TASK_007_cpp_cli_inspect_help
    PROPERTIES PASS_REGULAR_EXPRESSION "Inspect source framing"
  )

  add_test(NAME TASK_007_cpp_cli_replay_help COMMAND itchlab replay --help)
  set_tests_properties(
    TASK_007_cpp_cli_replay_help
    PROPERTIES PASS_REGULAR_EXPRESSION "versioned binary events"
  )

  add_test(NAME TASK_015_cpp_cli_validate_help COMMAND itchlab validate --help)
  set_tests_properties(
    TASK_015_cpp_cli_validate_help
    PROPERTIES PASS_REGULAR_EXPRESSION "Validate a completed replay"
  )
endif()
