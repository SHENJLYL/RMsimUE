cmake_minimum_required(VERSION 3.16)
project(sp_vision_25 LANGUAGES CXX)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(MSVC)
  add_compile_options(/W4 /utf-8 /bigobj)
  add_compile_definitions(NOMINMAX _USE_MATH_DEFINES)
else()
  add_compile_options(-Wall -Wextra -O3)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(ament_index_cpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(OpenCV REQUIRED)
find_package(nlohmann_json REQUIRED)

find_package(sp_msgs QUIET)
find_package(dji_referee_protocol QUIET)
find_package(OpenVINO QUIET)

# Static core library (zero dllexport boilerplate on Windows, fast LTO/inlining on Ubuntu)
add_library(sp_vision_25_core STATIC
  src/detector.cpp
  src/solver_tracker.cpp
)
target_include_directories(sp_vision_25_core PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
  ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(sp_vision_25_core PUBLIC
  ${OpenCV_LIBS}
)
if(OpenVINO_FOUND)
  message(STATUS "sp_vision_25: Found OpenVINO C++ runtime - enabling native ov::Core acceleration")
  target_compile_definitions(sp_vision_25_core PUBLIC SP_VISION_HAS_OPENVINO)
  target_link_libraries(sp_vision_25_core PUBLIC openvino::runtime)
else()
  message(STATUS "sp_vision_25: OpenVINO C++ SDK not found - using OpenCV DNN (ModelOptimizer + ONNX)")
endif()

# Main ROS 2 auto-aim node executable (matches launch/vision_autoaim.launch.py executable="vision_node")
add_executable(vision_node src/vision_node.cpp)
target_link_libraries(vision_node
  sp_vision_25_core
  nlohmann_json::nlohmann_json
)
ament_target_dependencies(vision_node
  rclcpp
  ament_index_cpp
  sensor_msgs
  geometry_msgs
  std_msgs
)
if(sp_msgs_FOUND)
  message(STATUS "sp_vision_25: Found sp_msgs - enabling AutoAimCommandMsg publisher")
  target_compile_definitions(vision_node PRIVATE SP_VISION_HAS_SP_MSGS)
  ament_target_dependencies(vision_node sp_msgs)
else()
  message(WARNING "sp_vision_25: sp_msgs not found - AutoAimCommandMsg publisher disabled")
endif()

if(dji_referee_protocol_FOUND)
  message(STATUS "sp_vision_25: Found dji_referee_protocol - enabling native real-robot referee subscribers")
  target_compile_definitions(vision_node PRIVATE SP_VISION_HAS_DJI_REFEREE)
  ament_target_dependencies(vision_node dji_referee_protocol)
else()
  message(STATUS "sp_vision_25: dji_referee_protocol not found - simulator JSON referee mode only")
endif()

# Standalone offline benchmark & accuracy verifier
add_executable(sp_vision_offline_verify src/offline_verify.cpp)
target_link_libraries(sp_vision_offline_verify
  sp_vision_25_core
  nlohmann_json::nlohmann_json
)

install(TARGETS
  vision_node
  sp_vision_offline_verify
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY
  assets
  config
  launch
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
