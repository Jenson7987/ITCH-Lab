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
  cpp/src/config/canonical_json.cpp
  cpp/src/config/replay_config.cpp
  cpp/src/core/errors.cpp
  cpp/src/core/sha256.cpp
  cpp/src/input/byte_source.cpp
  cpp/src/input/file_source.cpp
  cpp/src/input/framed_reader.cpp
  cpp/src/input/gzip_source.cpp
)
target_include_directories(itchlab_core PUBLIC cpp/include)
target_link_libraries(itchlab_core PRIVATE nlohmann_json::nlohmann_json ZLIB::ZLIB)
itchlab_configure_target(itchlab_core)

add_executable(itchlab cpp/apps/itchlab/main.cpp)
target_compile_definitions(itchlab PRIVATE ITCHLAB_VERSION="${PROJECT_VERSION}")
target_link_libraries(itchlab PRIVATE itchlab_core)
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
    tests/cpp/property/framed_reader_property_test.cpp
    tests/cpp/unit/byte_source_test.cpp
    tests/cpp/unit/errors_test.cpp
    tests/cpp/unit/framed_reader_test.cpp
    tests/cpp/unit/sha256_test.cpp
    tests/cpp/unit/types_test.cpp
  )
  target_compile_definitions(
    itchlab_core_tests PRIVATE ITCHLAB_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
  )
  target_link_libraries(itchlab_core_tests PRIVATE itchlab_core Catch2::Catch2WithMain)
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
endif()
