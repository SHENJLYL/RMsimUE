# RMUC 红蓝 3、4 号步兵外观

四款共用同一个 `aim_sentry` 车辆包以及现有四面装甲覆盖层。只替换正面的编号，原装甲边缘、安装孔和灯条遮罩沿用哨兵资源。绑定后的阵营灯条仍由比赛中的实际红蓝队色驱动。车体尺寸、灯条槽位、碰撞和动力学由原车辆包提供。

数字采用 RMUC 2026 制作规范 V1.2.0 的附录图 3、4。生成器以官方图里的装甲比例放大数字到当前 1080 × 1000 贴纸上，沿用原底色和 Alpha。 [纹理源与生成方式](../../assets/ui/armor_stickers/SOURCE.md)

编队中每侧的 `armor_number` 可独立填 3 或 4。省略编号或填 7 使用默认哨兵图案。两个可直接启动的样例覆盖红 3／蓝 4 和红 4／蓝 3，比赛席位继续使用 AIM 的原有模型及控制规则。

```powershell
powershell.exe -NoProfile -File tools/unreal/Start-Simulator.ps1 -SpawnId red -MatchRoster assets/vehicles/aim_sentry/appearances/infantry-red3-blue4.json
```

Windows UE 5.8 编译成功。红 3／蓝 4、红 4／蓝 3 两场使用原生取景相机核验，分别确认每车 4 个装甲面，合计 16 个面编号朝向、居中与阵营灯条通过目视检查。原生装甲验收两个场次均退出 0。比赛启动器 6 项回归通过。 [四款实拍](../../artifacts/unreal/rmuc-infantry-appearance-20261126/infantry-four-variants.png) · [16 面总览](../../artifacts/unreal/rmuc-infantry-appearance-20261126/sixteen-faces.png) · [验收记录](../../artifacts/unreal/rmuc-infantry-appearance-20261126/release-summary.json)
