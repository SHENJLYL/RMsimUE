# sp_vision_25 (RMUC 2027 仿真与实车统一自瞄包)

同一套 ROS 2 节点（`sp_vision_25/vision_node`）同时支持 **UE5 仿真** 与 **Ubuntu 实车比赛部署**，内含全部模型权重与前哨站贴纸模板（`assets/yolov8.xml`、`yolov8.bin`、`tiny_resnet.onnx`、`outpost_sticker.png`），拷贝即用。

## 实车部署步骤（Ubuntu ROS 2 Humble）

### 1. 编译安装
在实车工作空间放入 `sp_vision_25`、`sp_msgs`、`dji_referee_protocol`（以及可选的 `behavior`）：
```bash
cd ~/ros2_ws
colcon build --packages-up-to sp_vision_25 behavior --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
```

### 2. 配置实车话题与标定参数
编辑 `config/real_robot.yaml`：
- **相机话题**：`camera_topic`、`camera_info_topic`（节点会自动读取 `CameraInfo.k` 内参矩阵和 `CameraInfo.d` 畸变系数用于 PnP 解算）。
- **云台姿态同步**：
  - 若下位机/驱动发布关节角，设置 `joint_state_topic`（例如 `/aim_sentry/joint_states`）；
  - 若下位机直接发布世界系四元数 IMU，设置 `imu_topic`（例如 `/gimbal/imu`）；
  - 节点会对云台姿态做**时间戳线性插值**并与相机帧时间戳对齐，避免高速小陀螺时的相位滞后；
  - 若下位机 Pitch 定义为**抬头为正**，将 `pitch_up_positive: true`。
- **机械与弹道标定**：
  - `yaw_offset_deg` / `pitch_offset_deg`：相机光轴与枪管零位偏置；
  - `cam_to_barrel_z_m`：相机高于枪管轴线的垂直距离（米）；
  - `latency_s`：从曝光到拨弹发射的全链路延迟（秒，实车一般 `0.06~0.11`）；
  - `muzzle_speed_mps`：初始弹速（比赛期间会自动根据 `/referee/common/shoot_data` 实测初速平滑自适应）。

### 3. 启动方式
- **单视觉台架调试（不接裁判系统/行为树，开启调试画面）**：
  ```bash
  ros2 launch sp_vision_25 vision_autoaim.launch.py mode:=real enemy_color:=blue require_behavior_switch:=false debug_image:=true
  ```
  可在 RViz2 / Foxglove 中订阅 `/sp_vision/debug_image` 和 `/sp_vision/status` 观察检测框、距离、预测提前量与开火状态。
- **正式比赛（接入 `dji_referee_protocol` + `behavior` 行为树）**：
  ```bash
  ros2 launch sp_vision_25 vision_autoaim.launch.py mode:=real
  ```
