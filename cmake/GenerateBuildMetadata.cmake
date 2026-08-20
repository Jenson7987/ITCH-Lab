set(ITCHLAB_METADATA_REVISION "unknown")
set(ITCHLAB_METADATA_DIRTY 1)

if(NOT "${ITCHLAB_METADATA_GIT}" STREQUAL "")
  execute_process(
    COMMAND "${ITCHLAB_METADATA_GIT}" rev-parse HEAD
    WORKING_DIRECTORY "${ITCHLAB_METADATA_SOURCE_DIR}"
    RESULT_VARIABLE revision_result
    OUTPUT_VARIABLE revision_output
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
  )
  if(revision_result EQUAL 0)
    set(ITCHLAB_METADATA_REVISION "${revision_output}")
  endif()

  execute_process(
    COMMAND "${ITCHLAB_METADATA_GIT}" status --porcelain --untracked-files=normal
    WORKING_DIRECTORY "${ITCHLAB_METADATA_SOURCE_DIR}"
    RESULT_VARIABLE status_result
    OUTPUT_VARIABLE status_output
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
  )
  if(status_result EQUAL 0 AND status_output STREQUAL "")
    set(ITCHLAB_METADATA_DIRTY 0)
  endif()
endif()

function(escape_cpp_string input output)
  string(REPLACE "\\" "\\\\" escaped "${input}")
  string(REPLACE "\"" "\\\"" escaped "${escaped}")
  set(${output} "${escaped}" PARENT_SCOPE)
endfunction()

escape_cpp_string("${ITCHLAB_METADATA_VERSION}" version)
escape_cpp_string("${ITCHLAB_METADATA_REVISION}" revision)
escape_cpp_string("${ITCHLAB_METADATA_COMPILER}" compiler)
escape_cpp_string("${ITCHLAB_METADATA_COMPILER_VERSION}" compiler_version)
escape_cpp_string("${ITCHLAB_METADATA_TARGET}" target)
escape_cpp_string("${ITCHLAB_METADATA_BUILD_TYPE}" build_type)
escape_cpp_string("${ITCHLAB_METADATA_BUILD_FLAGS}" build_flags)

get_filename_component(output_directory "${ITCHLAB_METADATA_OUTPUT}" DIRECTORY)
file(MAKE_DIRECTORY "${output_directory}")
set(temporary "${ITCHLAB_METADATA_OUTPUT}.tmp")
file(WRITE "${temporary}"
"#pragma once

#define ITCHLAB_VERSION \"${version}\"
#define ITCHLAB_GIT_REVISION \"${revision}\"
#define ITCHLAB_GIT_DIRTY ${ITCHLAB_METADATA_DIRTY}
#define ITCHLAB_COMPILER_ID \"${compiler}\"
#define ITCHLAB_COMPILER_VERSION \"${compiler_version}\"
#define ITCHLAB_TARGET \"${target}\"
#define ITCHLAB_BUILD_TYPE \"${build_type}\"
#define ITCHLAB_BUILD_FLAGS \"${build_flags}\"
")
execute_process(COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${temporary}" "${ITCHLAB_METADATA_OUTPUT}")
file(REMOVE "${temporary}")
