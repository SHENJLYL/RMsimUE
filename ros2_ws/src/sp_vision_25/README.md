# sp_vision_25 (C++17 ROS 2 Humble — 仿真 & 实车通用自瞄包)

纯 C++17 ROS 2 Humble 自瞄节点，**同一套代码、同一套权重、同一套 ROS 2 接口**无缝运行在 **Windows UE5 仿真** 与 **Ubuntu 实车 (Intel NUC / x86 / ARM)** 上：

- **双引擎装甲板检测**：YOLOv8 (`assets/yolov8.xml` + `yolov8.bin`, 416×416 关键点检测) + 全分辨率 1280×720 HSV 灯条配对 + 前哨站贴纸模板 (`assets/outpost_sticker.png`) + `tiny_resnet.onnx` 9 类数字分类器。
- **自动推理后端切换**：CMake 检测到 Intel OpenVINO C++ SDK 时自动启用原生 `ov::Core` 低延迟推理；未安装 OpenVINO SDK 时自动回退到 OpenCV DNN (`readNetFromModelOptimizer` + `readNetFromONNX`)，开箱即用。
- **解算与追踪**：`cv::solvePnP(SOLVEPNP_IPPE)`（支持真实相机畸变 `D`）+ 世界坐标系角速度追踪（免疫小陀螺、前哨站三高度零滞后 Pitch 与旋转提前量）+ 17mm 弹道重力下坠/飞行时间/延迟提前量/相机-枪管视差补偿。
- **零硬件 SDK 耦合**：直接解析 `sensor_msgs/Image`（无需 `cv_bridge`），自动适配 `JointState` / `Imu` 姿态插值、仿真 JSON 裁判系统与实车原生 `dji_referee_protocol`。

## 1. Windows 仿真环境编译与运行

```powershell
# 编译 C++17 视觉包到 ros2_ws/install_vision
.\tools\environment\Build-Vision.ps1 -Clean

# 离线帧精度与 C++ 耗时基准测试
.\tools\environment\run-env.ps1 .\ros2_ws\install_vision\sp_vision_25\lib\sp_vision_25\sp_vision_offline_verify.exe

# 独立启动视觉节点（external_sentry_connector.py 也会自动优先调用 C++ vision_node.exe）
.\tools\environment\Start-VisionAutoAim.ps1 -RobotId aim_sentry -RequireBehaviorSwitch
```

## 2. Ubuntu 22.04 (ROS 2 Humble) 实车编译与部署

安装依赖（可选安装 `openvino` C++ 运行时以在 NUC 上获得最高帧率）：

```bash
sudo apt update && sudo apt install -y libopencv-dev nlohmann-json3-dev
```

在实车工作空间中编译（含 `sp_msgs`、`dji_referee_protocol`、`sp_vision_25`）：

```bash
cd ~/rm_ws
colcon build --packages-up-to sp_vision_25 --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
```

### 2.1 车库标定 / 单节点调试（无需裁判系统与行为树）

```bash
ros2 launch sp_vision_25 vision_autoaim.launch.py \
  mode:=real enemy_color:=blue require_behavior_switch:=false debug_image:=true
```

在 RViz2 / Foxglove 中订阅 `/sp_vision/debug_image` 检查识别框、PnP 距离和弹着点误差。

### 2.2 完整实车比赛运行

编辑 `config/real_robot.yaml` 对齐你的相机话题、云台姿态源（`joint_state_topic` 或 `imu_topic`）、`pitch_up_positive` 符号及相机-枪管偏移（`yaw_offset_deg` / `pitch_offset_deg` / `cam_to_barrel_z_m` / `latency_s`），然后启动：

```bash
ros2 launch sp_vision_25 vision_autoaim.launch.py mode:=real
```
