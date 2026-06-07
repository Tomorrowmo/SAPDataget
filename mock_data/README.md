# Mock 数据集

服务于 `MockBWClient`（详见 [需求分析与技术方案.md](../需求分析与技术方案.md) §8.5）。

## 结构

```
mock_data/
├── README.md                 本文件
├── catalog.json              服务目录（list_services 返回）
└── services/
    ├── ZBW_SALES_SRV/        销售分析
    │   ├── meta.json         EntitySet/字段定义
    │   └── data/             每个 EntitySet 一个 CSV
    ├── ZBW_INV_SRV/          库存分析
    ├── ZBW_FIN_SRV/          财务报表
    ├── ZBW_PROD_SRV/         生产分析
    └── ZBW_PROC_SRV/         采购分析
```

## 业务场景

设定为一家工业制造业公司（参考西门子风格），覆盖典型的 BW 报表场景：

| 服务 | EntitySet | 用途 |
|---|---|---|
| ZBW_SALES_SRV | SalesByOfficeView | 月度各办事处销售额（含同比、环比） |
| ZBW_SALES_SRV | SalesByCustomer  | 客户销售排名 |
| ZBW_SALES_SRV | SalesOrderHeader | 订单明细 |
| ZBW_INV_SRV   | StockByMaterial  | 物料库存周转 |
| ZBW_INV_SRV   | StockMovement    | 库存出入移动 |
| ZBW_FIN_SRV   | GLBalance        | 总账余额 |
| ZBW_FIN_SRV   | ARAging          | 应收账款账龄 |
| ZBW_FIN_SRV   | APAging          | 应付账款账龄 |
| ZBW_PROD_SRV  | ProductionOrder  | 生产订单状态 |
| ZBW_PROD_SRV  | YieldByPlant     | 工厂良率 |
| ZBW_PROC_SRV  | PurchaseOrder    | 采购订单跟踪 |

## 数据规模

每 CSV 50-500 行，覆盖足够的维度组合用于演示与回归测试。

## 字段命名

刻意贴近真实 SAP BW 命名规范：
- `NETWR_F` （净销售额）
- `0CALMONTH`（年月）
- `/BIC/ZCUST`（自定义客户）
- `WERKS`（工厂代码）
等等，方便 LLM 在 Mock 上学到的模式迁移到真实 BW。
