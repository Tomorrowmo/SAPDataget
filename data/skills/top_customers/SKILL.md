---
id: top_customers
title: 客户销售排名
description: 按某月份的净销售额排序,列出前 N 名客户。
owner: BW 团队
version: 1
keywords: [客户, 排名, 销售, top, customer, ranking]
params:
  - name: month
    required: true
    description: 年月 YYYYMM
  - name: top_n
    required: false
    default: 20
    description: 展示前几名,默认 20
  - name: region
    required: false
    description: 可选,只看某个大区 (HD/HN/HB/HX/XB/DB)
    enum: [HD, HN, HB, HX, XB, DB]
---

# 客户销售排名

## 适用场景
销售/财务部门看某月业绩贡献客户清单。

## 给 LLM 的指引
- 用户说"上月" / "本月" 换算 YYYYMM
- 用户没提大区就不要传 region 参数（系统会取全国）
