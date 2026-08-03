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

add_executable(itchlab cpp/apps/itchlab/main.cpp)
target_compile_features(itchlab PRIVATE cxx_std_20)
set_target_properties(itchlab PROPERTIES CXX_EXTENSIONS OFF)
target_compile_definitions(itchlab PRIVATE ITCHLAB_VERSION="${PROJECT_VERSION}")

if(MSVC)
  target_compile_options(itchlab PRIVATE /W4)
  if(ITCHLAB_WARNINGS_AS_ERRORS)
    target_compile_options(itchlab PRIVATE /WX)
  endif()
elseif(CMAKE_CXX_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
  target_compile_options(
    itchlab
    PRIVATE
      -Wall
      -Wextra
      -Wpedantic
      -Wconversion
      -Wsign-conversion
  )
  if(ITCHLAB_WARNINGS_AS_ERRORS)
    target_compile_options(itchlab PRIVATE -Werror)
  endif()
endif()

if(ITCHLAB_ENABLE_SANITIZERS)
  if(CMAKE_CXX_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
    target_compile_options(itchlab PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(itchlab PRIVATE -fsanitize=address,undefined)
  else()
    message(FATAL_ERROR "The sanitizers preset requires Clang, AppleClang or GCC")
  endif()
endif()

if(ITCHLAB_ENABLE_COVERAGE)
  if(CMAKE_CXX_COMPILER_ID MATCHES "AppleClang|Clang|GNU")
    target_compile_options(itchlab PRIVATE -O0 -g --coverage)
    target_link_options(itchlab PRIVATE --coverage)
  else()
    message(FATAL_ERROR "The coverage preset requires Clang, AppleClang or GCC")
  endif()
endif()

include(CTest)
if(BUILD_TESTING)
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
