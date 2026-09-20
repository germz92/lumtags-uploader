cmake_minimum_required(VERSION 3.21)
if(APPLE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "12.1" CACHE STRING "Minimum macOS version")
endif()
project(crsdk_host LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(crsdk_host
    src/main.cpp
    src/protocol.cpp
    src/backend_simulator.cpp
)

target_include_directories(crsdk_host PRIVATE src)

# Direct PTP backend: we own the USB interface instead of handing it to Sony's
# adapter. macOS only for now; Windows would need a WinUSB implementation.
if(APPLE)
    target_sources(crsdk_host PRIVATE
        src/usb_darwin.cpp
        src/ptp.cpp
        src/ptp_sony.cpp
        src/backend_ptp.cpp
    )
    target_compile_definitions(crsdk_host PRIVATE PTP_BACKEND_AVAILABLE=1)
    target_link_libraries(crsdk_host PRIVATE
        "-framework IOKit"
        "-framework CoreFoundation"
    )

    add_executable(ptp_probe
        src/ptp_probe.cpp
        src/usb_darwin.cpp
        src/ptp.cpp
        src/ptp_sony.cpp
    )
    target_include_directories(ptp_probe PRIVATE src)
    target_link_libraries(ptp_probe PRIVATE
        "-framework IOKit"
        "-framework CoreFoundation"
    )
endif()

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
        # Sony's sample only links Cr_Core; adapters load at runtime from CrAdapter.
        find_library(CRSDK_CORE Cr_Core HINTS
            "${CRSDK_ROOT}/external/crsdk"
            "${CRSDK_ROOT}/lib"
        )
        if(CRSDK_CORE)
            target_link_libraries(crsdk_host PRIVATE ${CRSDK_CORE})
        else()
            file(GLOB CRSDK_LIBS
                "${CRSDK_ROOT}/lib/*.dylib"
                "${CRSDK_ROOT}/external/crsdk/*.dylib"
            )
            if(CRSDK_LIBS)
                target_link_libraries(crsdk_host PRIVATE ${CRSDK_LIBS})
            endif()
        endif()
        set_target_properties(crsdk_host PROPERTIES
            BUILD_RPATH "@executable_path"
            INSTALL_RPATH "@executable_path"
        )
        # Official Mac layout: dylibs next to the binary, adapters in
        # Contents/Frameworks/CrAdapter (as if the host lived in an .app).
        # Also keep CrAdapter beside the binary so source runs work.
        if(EXISTS "${CRSDK_ROOT}/external/crsdk")
            add_custom_command(TARGET crsdk_host POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_directory
                    "${CRSDK_ROOT}/external/crsdk"
                    $<TARGET_FILE_DIR:crsdk_host>
                COMMAND ${CMAKE_COMMAND} -E make_directory
                    $<TARGET_FILE_DIR:crsdk_host>/Contents/Frameworks/CrAdapter
                COMMAND ${CMAKE_COMMAND} -E copy_directory
                    $<TARGET_FILE_DIR:crsdk_host>/CrAdapter
                    $<TARGET_FILE_DIR:crsdk_host>/Contents/Frameworks/CrAdapter
            )
        endif()
    endif()
else()
    message(STATUS "CRSDK_ROOT not set — building protocol simulator only")
endif()
