cmake_minimum_required(VERSION 3.16)

project(mestimate_sidecar C)

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET
    libavformat
    libavcodec
    libavfilter
    libavutil
    libswscale
)
pkg_check_modules(ZLIB REQUIRED IMPORTED_TARGET zlib)

add_executable(mestimate-sidecar
    src/mestimate_sidecar.c
)

target_include_directories(mestimate-sidecar PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(mestimate-sidecar PRIVATE
    PkgConfig::FFMPEG
    PkgConfig::ZLIB
    m
)

target_compile_definitions(mestimate-sidecar PRIVATE
    MESTIMATE_SIDECAR_VERSION="0.1.0"
    MESTIMATE_BUILD_TYPE="$<CONFIG>"
    MESTIMATE_COMPILER="${CMAKE_C_COMPILER_ID} ${CMAKE_C_COMPILER_VERSION}"
)

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(mestimate-sidecar PRIVATE -Wall -Wextra -Wpedantic)
endif()
