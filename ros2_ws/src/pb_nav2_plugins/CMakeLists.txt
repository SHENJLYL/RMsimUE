cmake_minimum_required(VERSION 3.5)
project(pb_nav2_plugins)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

## By adding -Wall and -Werror, the compiler does not ignore warnings anymore,
## enforcing cleaner code.
if(MSVC)
  add_compile_options(/bigobj /utf-8 /EHsc)
  add_compile_definitions(NOMINMAX _USE_MATH_DEFINES)
  set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
else()
  add_compile_options(-Wall -Werror)
endif()

## Export compile commands for clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

ament_auto_add_library(pb_back_up_frees_space_behavior SHARED
  src/behaviors/back_up_free_space.cpp
)

ament_auto_add_library(layers SHARED
  src/layers/intensity_voxel_layer.cpp
)
# Nav2 itself also ships layers.dll; keep this plugin's binary identity distinct.
set_target_properties(layers PROPERTIES OUTPUT_NAME pb_nav2_layers)

if(BUILD_TESTING)
  set(ament_cmake_clang_format_CONFIG_FILE "${CMAKE_SOURCE_DIR}/.clang-format")
  set(ament_cmake_clang_tidy_CONFIG_FILE "${CMAKE_SOURCE_DIR}/.clang-tidy")
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

pluginlib_export_plugin_description_file(nav2_core behavior_plugin.xml)
pluginlib_export_plugin_description_file(nav2_costmap_2d costmap_plugins.xml)

ament_auto_package(
  USE_SCOPED_HEADER_INSTALL_DIR
)
