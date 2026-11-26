# RMUC 装甲命中音效统一

弹丸结算区分有效装甲命中、能量机关命中和普通碰撞。车辆、前哨和基地的有效命中均以 `metal_impact.wav` 在实际命中点播放，基础音量 0.62、播放音高 1.0，最终听感随距离衰减。护盾、低冲击和板外碰撞沿用原材质音效。裁判已有的操控侧命中确认声继续随命中回执播放。

复用项目自带的金属板实录。 [录音与许可](../../assets/audio/rmuc2027/SOURCES.json)

`Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 返回成功。Windows 原生离屏以真实音频设备运行项目已有的弹丸和场地验证，退出码 0。16 个车辆装甲采样、敌方前哨和基地小装甲各播放了恰好一次相同录音。护盾击中、边缘和低冲击未触发装甲音，地面、玻璃和车身在各自碰撞中触发对应材质音。测试地图只改蓝方验证出生点，使三类命中位于空间音效可听距离内。 [七项验收和构建指纹](../../artifacts/unreal/rmuc-armor-audio-20261125T085532Z/verification.json) · [默认出生点的距离对照](../../artifacts/unreal/rmuc-armor-audio-20261125T085418Z/verification.json)
