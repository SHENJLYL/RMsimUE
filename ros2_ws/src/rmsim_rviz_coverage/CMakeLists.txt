cmake_minimum_required(VERSION 3.16)
project(rmsim_rviz_coverage LANGUAGES CXX)

find_package(ament_cmake REQUIRED)
if(WIN32)
  find_package(pluginlib REQUIRED)
  find_package(rviz_common REQUIRED)
  find_package(rviz_default_plugins REQUIRED)
  find_package(rviz_rendering REQUIRED)
  find_package(rviz_ogre_vendor REQUIRED)
  find_package(Qt5 REQUIRED COMPONENTS Widgets)

  add_library(${PROJECT_NAME} SHARED src/fullscreen_coverage.cpp)
  target_compile_features(${PROJECT_NAME} PRIVATE cxx_std_17)
  target_compile_definitions(${PROJECT_NAME} PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
  target_link_libraries(${PROJECT_NAME} PRIVATE
    pluginlib::pluginlib rviz_common::rviz_common rviz_default_plugins::rviz_default_plugins rviz_rendering::rviz_rendering
    OgreMain Qt5::Widgets dwmapi user32)
  pluginlib_export_plugin_description_file(rviz_common plugins.xml)
  install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION bin)
endif()
ament_package()
