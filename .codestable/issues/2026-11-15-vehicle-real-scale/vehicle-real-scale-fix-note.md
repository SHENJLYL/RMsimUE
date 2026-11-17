---
doc_type: issue-fix-note
slug: vehicle-real-scale
status: verified
summary: Restore original robot scale and remove a false whole-body ramp obstruction.
tags: [vehicle, scale, collision, regression]
---

# 尺寸与移动修复

原WT sentry_spec/rig_manifest的linear_scale为2，build_model在毫米转米后把网格和节点平移统一乘2。本项目最初知道并记录了这个倍率，却错误保留为机器人尺寸。现在导出时统一除以源倍率，不修改原WT文件。

当前中立姿态503.850×501.985×419.192mm，轮半径42.5mm。25项比例检查覆盖7部件边界/关节、整车、炮口、相机，均为之前一半。实际UE7部件边界与新源清单最大误差0.000003159cm。源文件哈希不变。原STEP记录路径现不可用，但保留的原毫米PLY及pre-scale rig记录已直接核对。WT炮管等局部外观改装仍保留，不把尺寸恢复夸大为原CAD完整还原。

用户同时报告RMUC中几乎不能移动。默认出生点原生共用输入检查可复现正常前进/倒退/转向；最终9项通过，1秒转向58.9535°，转向后1秒前进91.5cm。无法把这个局部结果扩大为全部场地均可通行。

另有确证碰撞错误：中央12°坡面，整体box底角穿入2.65mm，而实际所有模型顶点离坡面至少19.31mm。地形查询改为7个稳定部件box分别重叠/扫掠，保留轮子参考姿态和旋转途中覆盖，正常墙体仍阻挡。原生测试沿源坡道前进160cm、上升14.1829cm且全程不重叠，2项/4PNG通过。HUD现在显示地形阻挡原因，便于进一步定位。

证据根artifacts/assets/scale-fix-20261115：before保留旧导出；export/verify记录只读源与FBX检查；scale-ratios和ue-scale-verification核验尺度；rmuc-motion-final9项，ramp-parts-01坡道，combat-final14项。旧18项操控回归结果另见controls-final。

最终窗口已重开，实际画面观察到原尺寸车辆在RMUC行驶，HUD速度4.7km/h。live-input.json保存真实输入与状态；窗口保留给用户，不继续抢占其操作。29个原始资产/脚本哈希再次核对未变。

限制：运动学、2cm间隙、5cm支撑台阶限制仍非真实轮地动力学。21–22°坡折处部件box可能仍过于保守；低于车体的矮洞不能靠关闭碰撞穿过。更贴合的物理碰撞体和悬挂属于后续功能，当前未声称全部路线已验收。
