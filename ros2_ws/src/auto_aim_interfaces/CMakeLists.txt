cmake_minimum_required(VERSION 3.8)
project(auto_aim_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(std_msgs REQUIRED)

option(RMSIM_AUTO_AIM_INTERFACES_MINIMAL "Generate only the sentry behavior's vision messages" OFF)
set(aim_messages
  "msg/Armor.msg"
  "msg/Armors.msg"
  "msg/Target.msg"
)

if(NOT RMSIM_AUTO_AIM_INTERFACES_MINIMAL)
  list(APPEND aim_messages
    "msg/DebugLight.msg"
    "msg/DebugLights.msg"
    "msg/DebugArmor.msg"
    "msg/DebugArmors.msg"
    "msg/TrackerInfo.msg"
  )
endif()

rosidl_generate_interfaces(${PROJECT_NAME}
  ${aim_messages}
  DEPENDENCIES
    std_msgs
    geometry_msgs
)


ament_export_dependencies(rosidl_default_runtime)
ament_package()
