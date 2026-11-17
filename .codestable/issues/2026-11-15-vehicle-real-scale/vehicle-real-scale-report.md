---
doc_type: issue-report
slug: vehicle-real-scale
status: confirmed
summary: Manual sample retained the original War Thunder enlargement instead of real robot dimensions.
tags: [vehicle, scale, asset]
---

用户报告实车只有当前模型一半大，并要求检查原始模型。现包asset_import.json明确source_linear_scale_vs_cad=2和export_scale_vs_blend=1；原WT sentry_spec.json为linear_scale=2，README称长宽高各2倍，build_model.py将倍率用于原CAD。错误在本项目导出策略：记录了WT展示倍率，却没有按机器人真实尺寸去除。

修复：导出网格、关节/轮位、轮径、炮口统一除以源linear_scale，保留所有原文件不变。相机相对炮口的演示偏移也同步缩放。地图单位、速度、伤害/热量等不作比例修改；质量/惯量仍未标定，不能从WT3吨推断。

验证：重新读取源blend/CAD来源，保留旧导出；重导7FBX及UE资产；核验尺寸/所有部件/pivot/轮径/炮口/相机均为旧版一半，并核对实际UEbounds。运行受影响驾驶/瞄准/射击检查并保存新证据。
