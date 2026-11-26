cmake_minimum_required(VERSION 3.16)
project(rmsim_sentry_tactics)

find_package(ament_cmake REQUIRED)
find_package(behaviortree_ros2 REQUIRED)
find_package(example_interfaces REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(pb_rm_interfaces REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

add_library(rmuc2027_tactics SHARED src/rmuc2027_tactics.cpp)
target_compile_features(rmuc2027_tactics PRIVATE cxx_std_17)
target_compile_definitions(rmuc2027_tactics PRIVATE BT_PLUGIN_EXPORT)
ament_target_dependencies(rmuc2027_tactics
  behaviortree_ros2 example_interfaces geometry_msgs pb_rm_interfaces rclcpp std_msgs)
target_link_libraries(rmuc2027_tactics nlohmann_json::nlohmann_json)
install(TARGETS rmuc2027_tactics
  LIBRARY DESTINATION share/${PROJECT_NAME}/bt_plugins
  RUNTIME DESTINATION share/${PROJECT_NAME}/bt_plugins
  ARCHIVE DESTINATION lib)
install(DIRECTORY behavior_trees DESTINATION share/${PROJECT_NAME})

ament_package()
