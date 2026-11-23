# 弗兰德斯源物件：柔性植被与分级可破坏实体

日期：2026-11-18。工作树：D:/simulator-wt-vlaanderen，分支 feat/wt-vlaanderen-20261117。

## 接入

运行时代码在 `unreal/RMSim/Source/RMSim/WTFoliageCrushComponent.{h,cpp}`，
由 `ImportLabGameMode` 在保存关卡与原生接触车就绪后初始化。
源 FBX、RMI1 placement、源碰撞 FBX、原保存 World.umap 均无原位编辑。
不影响 `D:/simulator` 的 ROS 导航工作树。

实际保存关卡启动后：31 个可破坏分组，4,647 个软植被实例解除对应硬碰撞，
1,498 个木板/木围栏/咖啡馆轻型围栏等破坏候选继续保持原生硬碰撞，直到实际触发。
源砖墙、石墙、道路、钢轨、建筑及真正树干维持其原碰撞策略。
总4,647包含4,404个命名 `tree_*_bush`、145个 `bush_flowers` 和98个花坛花卉实例。
花坛的实体底座始终保持源碰撞。

## 视觉与物理行为

- 灌木无需撞击刚体便能穿过；车体以至少0.4m/s经过根部135cm范围时，原 HISM 实例隐藏，
  以独立临时视觉网格按车辆方向柔性压倒、阻尼回弹，停留3.1秒后在1.2秒内缩小并销毁。
- 木围栏保留源三角碰撞；速度撞击或连续顶压达阈值后，对应原 HISM 实例的视觉与原碰撞同步局部隐藏；
  产生临时可摆动的倒伏网格及两个轻质量木屑代理块，3.8秒后开始逐渐消失，约5.1秒完成。
- 木板/轻质木废料用4个带原材质的动力学碎块代替原刚性物件；碎块接受Chaos重力、冲量、旋转，
  3.7秒后自动回收。当前碎块是按资源包围盒生成的分段代理；源精细断裂面的预制 Geometry Collection
  与木质贴花/粒子仍属后续视觉品质提升。
- 部分轻型金属围栏按更高阈值产生弯折/倾倒临时网格，5.1秒后销毁。
- 每个 HISM 实例独立跟踪；没有触发的相邻实体保留原碰撞。
- 查询车辆实际Chaos车轮/底盘 OnComponentHit，读取最近接触部件、位置、冲量。
  在接触前采样接近速度峰值，避免物理阻挡已经把车速降为零后漏判高速撞击。
  持续受压必须同时满足距离、指令方向、有效接近状态和车速受阻；计时离开时衰减。
  约13Hz 查询，最多48个活动倾倒网格，以控制长期场景成本。

## 参数

`unreal/RMSim/Config/DefaultGame.ini` → `[RMSim.Destruction]`：
`WoodImpactMps=2.2`、`MetalImpactMps=3.3`、
`WoodPressureSeconds=0.72`、`MetalPressureSeconds=1.4`。
额外动量阈值木类22 N·s、金属42 N·s也可触发高速碰撞分支。
配置只控制弗兰德斯样例易损物，不改变车辆原速度/扭矩、悬挂与地图原地形。

## Windows UE5.8.4 原生证据

- UBT `Build-ImportLab.ps1` 实际 exit 0，结果 `Result: Succeeded`。
- `artifacts/maps/wt-scene-20261118/destruction-contact-01`：
  原弗兰德斯车轮物理流程4/4项、6 PNG，exit0；运行期识别31组。
- `destruction-animation-native-01/runtime.log`：
  真实World场景中逐个触发软花灌木、轻金属围栏、花园木板和木围栏4类源实例。
  每个实例的原视觉和源碰撞局部收缩，其余实例保持；动画6.1秒后活动数0；
  `WT_DESTRUCTION_PROBE_PASSED`，exit0。
- `destruction-drive-fast-02/runtime.log`：
  真实车辆从原木围栏附近正常物理起步；第833个源实例在3.800秒被撞坏，
  源物理 hit=1，预撞接近速度峰值2.716m/s、当次冲量7.163N·s，
  `mode=impact`，exit0。
- `destruction-drive-hold-02/runtime.log`：
  同一源围栏在0.85m/s速度指令低速持续推压，累计有效压力0.778秒，
  当次冲量1.511N·s，`mode=pressure`，启动后7.078秒触发，exit0。
- 两个接触测试使用 `destruction-drive-fixture/map.json` 的临时出生点，
  不改原图文件、车位姿或真实物理约束。原地图重启时物件恢复；当前破坏不持久化。

## 范围

原图14,349个 `prop_russia_rails_sleeper_a` 和部分 `pavement_border` 无对应源碰撞，
本次未用视觉网格凭空代替碰撞：应以性能受控、明确几何形状的近车箱体或离线构建
进一步接入。原来源的重型设施保持源硬碰撞与完整路面连续性。

## 破坏后通过性补充（最终模块）

- 最后模块 SHA256: `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404`。
- `destruction-drive-cross-02/runtime.log`：同第833段木围栏实际预撞峰值2.727m/s、冲量8.149N·s；Chaos源碰撞物被局部清除，车继续穿过原围栏线90.29cm，穿越时实际速度3.018m/s；`mode=cross` 原生退出0。
- 最终模块代码绑定的正常弗兰德斯接触流程 `destruction-contact-02` 复测4/4项、6 PNG通过，exit0。
- 核心证据、所有更改后源码与UWorld/profiler身份的最终哈希快照保留在 `artifacts/maps/wt-scene-20261118/destruction-release-01/verification.json`。
