---
id: monthly_sales_region
title: 月度销售大区简报
description: 按办事处看某大区某月份的销售额、毛利、同比环比。
owner: BW 团队
version: 1
keywords: [销售, 大区, 月报, 月度, sales, region, monthly]
visible_to: []
params:
  - name: month
    required: true
    description: 年月 YYYYMM,例如 202605 表示 2026 年 5 月
  - name: region
    required: true
    description: 大区代码,HD/HN/HB/HX/XB/DB
    enum: [HD, HN, HB, HX, XB, DB]
  - name: top_n
    required: false
    default: 10
    description: 展示前几条
---

# 月度销售大区简报

## 适用场景
大区经理月初汇报当月业绩,需要按办事处看销售额、毛利、同比环比。

## 给 LLM 的指引
- 用户说"上月"、"上个月"应换算为对应 YYYYMM
- 用户说"华东"映射 HD;"华南" HN;"华北" HB;"华西" HX;"西北" XB;"东北" DB
- 销售额字段 NETWR_F (净销售额,单位万元),不要用其他字段
- 同比/环比已经在 BW 端预算好,直接查 YoY/MoM 列
- 输出表格列序: 排名 | 办事处 | 销售额 | 毛利 | 毛利率 | 同比 | 环比
