cmake_minimum_required(VERSION 3.10)
project(pb_omni_pid_pursuit_controller)

## Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
  add_compile_options(/bigobj /utf-8 /EHsc)
  add_compile_definitions(NOMINMAX _USE_MATH_DEFINES)
  set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
endif()

## Export compile commands for clangd and clang-tidy
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

ament_auto_add_library(${PROJECT_NAME} SHARED
  src/omni_pid_pursuit_controller.cpp
  src/pid.cpp
)

if(BUILD_TESTING)
  enable_testing()
  add_executable(swept_footprint_test test/swept_footprint_test.cpp)
  target_include_directories(swept_footprint_test PRIVATE include)
  add_test(NAME swept_footprint COMMAND swept_footprint_test)
  add_executable(pursuit_damping_test test/pursuit_damping_test.cpp src/pid.cpp)
  target_include_directories(pursuit_damping_test PRIVATE include)
  add_test(NAME pursuit_damping COMMAND pursuit_damping_test)
  set(ament_cmake_clang_format_CONFIG_FILE "${CMAKE_SOURCE_DIR}/.clang-format")
  set(ament_cmake_clang_tidy_CONFIG_FILE "${CMAKE_SOURCE_DIR}/.clang-tidy")
  find_package(ament_lint_auto REQUIRED)
    ament_lint_auto_find_test_dependencies()
endif()

pluginlib_export_plugin_description_file(nav2_core pb_omni_pid_pursuit_controller.xml)

ament_auto_package(
  USE_SCOPED_HEADER_INSTALL_DIR
)
