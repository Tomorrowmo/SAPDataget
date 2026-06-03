---
id: plant_yield
title: 工厂月度良率
description: 各工厂某月份的产量、不良数、良率、OEE 一览。
owner: BW 团队
version: 1
keywords: [良率, 工厂, OEE, 生产, 质量, yield, plant]
params:
  - name: month
    required: true
    description: 年月 YYYYMM
---

# 工厂月度良率

## 适用场景
生产/质量部门月度复盘。

## 给 LLM 的指引
- 良率 < 95% 的工厂应在回复里口头提醒重点关注
- OEE < 75% 的也建议提示
