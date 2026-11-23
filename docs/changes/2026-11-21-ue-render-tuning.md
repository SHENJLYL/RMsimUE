# RMUC 渲染调度与静止镜头阴影

## 渲染试验

同一 RMUC 地图、车辆和 2560×1440 输出使用固定帧索引的环绕镜头。每种配置保存十个实际 GPU 抓帧及两处同机位截图。下表记录包含 GPU 耗时中位数，各阶段可能重叠。

| 设置 | 场景视图阶段 | 相关阶段 | 结论 |
|---|---:|---:|---|
| 原设置两次 | 12.47 / 12.74 ms | 延迟光照 2.29 / 2.33 ms，透明 1.72 / 1.71 ms | 重复基线 |
| 聚类延迟光照 | 12.85 ms | 延迟光照 2.11 ms | 场景整体未获益 |
| TSR 全异步 | 14.41 ms | 后处理 3.64 ms | 比原设置慢 |
| TSR 历史缓冲 100% | 12.33 ms | 后处理 2.10 ms | 收益小于重复基线波动，画面差异增加 |
| 单独透明缓冲 80% | 12.94 ms | 透明 1.22 ms | 场景整体未获益，画面差异增加 |

正式设置保留 100% 主视口分辨率、原 TSR 历史、透明材质、灯具和阴影质量。原始试验位于 `artifacts/integration/gym-render-tuning/quality-screen-01`。 [Epic TSR 说明](https://dev.epicgames.com/documentation/unreal-engine/temporal-super-resolution-in-unreal-engine)

## 静止镜头阴影试验

候选按实际相机位置、方向、FOV 与视口尺寸判断稳定性。启动后引擎第 44 帧开启缓存，第 150 帧退出。追尾相机细微位移仍会令原生 CSM 重建，静止样本的阴影阶段从 2.67 ms 增至 5.15 ms。候选代码已撤回，保留 `stationary-csm-final` 的原始测量。

采样脚本记录固定与环绕区间的 GPU pass、逐帧 CSV 和固定机位截图。首次采样后的退出命令因不在有效采样帧范围内而超时。现使用 UE 的 `-ExitAfterCsvProfiling`。解析器也已支持 UE 引导帧的大事件字段，保留首轮原始 CSV。

## 导航渲染比例

移动镜头额外比较了 Lumen 主图形队列和关闭透明体积光照，GPU 整帧中位数分别为 16.87 和 16.17 ms，未超出原画质的基线收益范围。候选均只用于离屏诊断。

Windows 导航的 `-RenderScale 75` 在 2560×1440 输出下以 1920×1080 计算主画面，再用现有 TSR 重建。实际 GPU 抓帧确认该输入和输出尺寸。原生入口默认 100%，可选比例不改变物理、灯光、材质或窗口大小。移动镜头截图仍可辨认同一玻璃透射和阴影，细线与纹理有采样差异。

```powershell
& .\tools\environment\Start-NavigationConsole.ps1 -Fullscreen -Width 2560 -Height 1440 -ViewportHz 120 -RenderScale 75
```

`runtime-provenance.json` 与会话状态记录请求的 `render_scale_percent`。UE 在启动日志报告实际 `r.ScreenPercentage`。56 项 Windows 启动合同通过，包括 PowerShell 转发和实际 UE 参数构造。

最终同模块配对验证各取 38 个移动镜头帧。100% 与 75% 的 GPU 整帧中位数分别为 16.33 和 11.81 ms，降幅约 27.7%。UE 帧时间中位数为 16.69 和 12.18 ms。实际 75% 全屏导航就绪后运行 20 秒并正常退出，最后一段调试采样约 74.32 Draw/s，原生仿真倍率读数约 0.999×。对应原始记录为 `tsr-final-ab` 和 `tsr75-navigation`。

## 50% 全屏复测

用户指定的 `-RenderScale 50` 以 1280×720 渲染，再由 TSR 输出 2560×1440。原生导航会话正常启动和退出，Point-LIO、挂图、Nav2 与 RViz 就绪，1 ms MuJoCo 子步仍生效。排除会话退出后仍返回相同计数的三个重复采样，同一物理 epoch 的连续前台区间为 22.02 秒，场景平均 107.21 Draw/s，GPU 中位数 8.51 ms，仿真倍率 1.002×。该窗口使用静止车辆，显示器扫描呈现与持续运动的全程帧率未测。

同分辨率输出、同帧索引的移动镜头对照中，100%、75%、50% 的 GPU 帧耗时中位数分别为 16.33、11.81、8.58 ms。50% 较 100% 降低约 47.5%。截图可见栏杆、细边缘和地面线条的锐度差异。默认保持 100%，75% 和 50% 由用户启动时选用。 [三档画面对照](../../artifacts/integration/gym-render-tuning/render-scale-comparison.jpg) · [精简验证记录](../verification/2026-11-21-ue-render-tuning.json)
