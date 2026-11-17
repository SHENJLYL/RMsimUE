cmake_minimum_required(VERSION 3.14)
project(livox_ros_driver2 VERSION 1.1.0 LANGUAGES C CXX)
# Interface-only projection of pinned Livox source. No hardware SDK or driver node.
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/CustomPoint.msg" "msg/CustomMsg.msg"
  DEPENDENCIES std_msgs builtin_interfaces)
ament_export_dependencies(rosidl_default_runtime)
install(FILES LICENSE.txt README.md DESTINATION share/${PROJECT_NAME})
ament_package()
