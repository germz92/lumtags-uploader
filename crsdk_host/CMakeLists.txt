cmake_minimum_required(VERSION 3.21)
project(crsdk_host LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(crsdk_host
    src/main.cpp
    src/protocol.cpp
    src/backend_simulator.cpp
)

target_include_directories(crsdk_host PRIVATE src)

if(NOT CRSDK_ROOT AND DEFINED ENV{CRSDK_ROOT})
    set(CRSDK_ROOT "$ENV{CRSDK_ROOT}")
endif()

if(CRSDK_ROOT)
    message(STATUS "Building with Sony CRSDK at ${CRSDK_ROOT}")
    target_sources(crsdk_host PRIVATE src/backend_crsdk.cpp)
    target_compile_definitions(crsdk_host PRIVATE CRSDK_AVAILABLE=1)
    if(WIN32)
        target_compile_definitions(crsdk_host PRIVATE UNICODE _UNICODE)
    endif()
    target_include_directories(crsdk_host PRIVATE
        "${CRSDK_ROOT}"
        "${CRSDK_ROOT}/include"
        "${CRSDK_ROOT}/CRSDK"
        "${CRSDK_ROOT}/app/CRSDK"
    )
    if(WIN32)
        file(GLOB CRSDK_LIBS
            "${CRSDK_ROOT}/lib/*.lib"
            "${CRSDK_ROOT}/external/crsdk/*.lib"
            "${CRSDK_ROOT}/external/crsdk/Windows/x86_64/*.lib"
        )
        if(CRSDK_LIBS)
            target_link_libraries(crsdk_host PRIVATE ${CRSDK_LIBS})
        endif()
        if(EXISTS "${CRSDK_ROOT}/external/crsdk")
            add_custom_command(TARGET crsdk_host POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_directory
                    "${CRSDK_ROOT}/external/crsdk"
                    $<TARGET_FILE_DIR:crsdk_host>
            )
        endif()
    elseif(APPLE)
        file(GLOB CRSDK_LIBS
            "${CRSDK_ROOT}/lib/*.dylib"
            "${CRSDK_ROOT}/external/crsdk/*.dylib"
            "${CRSDK_ROOT}/external/crsdk/Mac/*.dylib"
            "${CRSDK_ROOT}/external/crsdk/MacOS/*.dylib"
            "${CRSDK_ROOT}/external/crsdk/macos/*.dylib"
        )
        if(CRSDK_LIBS)
            target_link_libraries(crsdk_host PRIVATE ${CRSDK_LIBS})
        endif()
        set_target_properties(crsdk_host PROPERTIES
            BUILD_RPATH "@executable_path;@executable_path/../lib"
            INSTALL_RPATH "@executable_path;@executable_path/../lib"
        )
    endif()
else()
    message(STATUS "CRSDK_ROOT not set — building protocol simulator only")
endif()
