cmake_minimum_required(VERSION 3.20)
project(butteraugli LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(WIN32)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" CACHE STRING "" FORCE)
endif()

set(TARGET butteraugli)

set(SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../deps/4D-Plugin-SDK/4D Plugin API")
set(BUTTERAUGLI_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../deps/butteraugli/butteraugli")

set(BUTTERAUGLI_SOURCES
    "${BUTTERAUGLI_DIR}/butteraugli.cc"
)

set(PLUGIN_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/butteraugli-4dplugin.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/stb_impl.c"
    "${SDK_DIR}/4DPluginAPI.c"
)

if(APPLE)
    add_library(${TARGET} MODULE ${PLUGIN_SOURCES} ${BUTTERAUGLI_SOURCES})
    set_target_properties(${TARGET} PROPERTIES
        BUNDLE TRUE
        BUNDLE_EXTENSION "bundle"
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in"
        MACOSX_BUNDLE_BUNDLE_NAME "${TARGET}"
        MACOSX_BUNDLE_GUI_IDENTIFIER "com.github.butteraugli"
        MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0.0"
        MACOSX_BUNDLE_BUNDLE_VERSION "1.0.0"
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/butteraugli-test/Plugins"
    )
    target_link_libraries(${TARGET} PRIVATE "-framework CoreGraphics")
elseif(WIN32)
    add_library(${TARGET} SHARED ${PLUGIN_SOURCES} ${BUTTERAUGLI_SOURCES})
    set_target_properties(${TARGET} PROPERTIES
        OUTPUT_NAME "${TARGET}"
        SUFFIX ".4DX"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/butteraugli-test/Plugins/${TARGET}/Contents/Windows64"
        RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/butteraugli-test/Plugins/${TARGET}/Contents/Windows64"
    )
    target_link_options(${TARGET} PRIVATE "/DEF:${SDK_DIR}/4DPluginAPI.def")
    target_compile_definitions(${TARGET} PRIVATE _CRT_SECURE_NO_WARNINGS)
    target_compile_options(${TARGET} PRIVATE /EHsc)
endif()

target_include_directories(${TARGET} PRIVATE
    "${SDK_DIR}"
    "${BUTTERAUGLI_DIR}/.."
    "${CMAKE_CURRENT_SOURCE_DIR}"
)

set_source_files_properties("${SDK_DIR}/4DPluginAPI.c" PROPERTIES LANGUAGE CXX)

# Copy resources to bundle
if(APPLE)
    set_source_files_properties(
        "${CMAKE_CURRENT_SOURCE_DIR}/manifest.json"
        "${CMAKE_CURRENT_SOURCE_DIR}/constants.xlf"
        PROPERTIES MACOSX_PACKAGE_LOCATION Resources
    )
    target_sources(${TARGET} PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/manifest.json"
        "${CMAKE_CURRENT_SOURCE_DIR}/constants.xlf"
    )
elseif(WIN32)
    add_custom_command(TARGET ${TARGET} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:${TARGET}>/../Resources"
        COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_SOURCE_DIR}/manifest.json" "$<TARGET_FILE_DIR:${TARGET}>/../Resources/"
        COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_SOURCE_DIR}/constants.xlf" "$<TARGET_FILE_DIR:${TARGET}>/../Resources/"
    )
endif()
