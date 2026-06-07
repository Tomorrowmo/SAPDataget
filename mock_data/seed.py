"""Mock 数据生成脚本 —— 确定性（固定 random seed）。

运行方式:
    python mock_data/seed.py

生成所有 5 个服务的 CSV 数据,覆盖典型工业制造业场景。
重新运行会覆盖旧数据。
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

# Windows 控制台 UTF-8 修复 —— 避免 ✓ 等字符 GBK 编码失败
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")            # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

random.seed(20260602)

ROOT = Path(__file__).resolve().parent
SERVICES = ROOT / "services"


# ============================== 公共维度 ==============================

REGIONS = [
    ("HD", "华东大区"),
    ("HN", "华南大区"),
    ("HB", "华北大区"),
    ("HX", "华西大区"),
    ("XB", "西北大区"),
    ("DB", "东北大区"),
]

OFFICES = [
    # (OfficeCode, OfficeName, Region)
    ("SHA", "上海办事处",   "HD"),
    ("NJG", "南京办事处",   "HD"),
    ("HZH", "杭州办事处",   "HD"),
    ("SZX", "深圳办事处",   "HN"),
    ("GZH", "广州办事处",   "HN"),
    ("XMN", "厦门办事处",   "HN"),
    ("BJG", "北京办事处",   "HB"),
    ("TJN", "天津办事处",   "HB"),
    ("CDU", "成都办事处",   "HX"),
    ("CQG", "重庆办事处",   "HX"),
    ("XAN", "西安办事处",   "XB"),
    ("URM", "乌鲁木齐办事处","XB"),
    ("SHY", "沈阳办事处",   "DB"),
    ("DLN", "大连办事处",   "DB"),
]

MONTHS = ["202512", "202601", "202602", "202603", "202604", "202605"]

CUSTOMERS = [
    # (KUNNR, CustomerName, Industry, Region)
    ("C100001", "上海张江工业集团",     "电子制造",     "HD"),
    ("C100002", "宁波港务实业",         "物流",         "HD"),
    ("C100003", "无锡新能科技",         "新能源",       "HD"),
    ("C100004", "苏州汽配股份",         "汽车零部件",   "HD"),
    ("C100005", "深圳华联电子",         "电子制造",     "HN"),
    ("C100006", "广州白云重工",         "重工",         "HN"),
    ("C100007", "佛山陶瓷集团",         "建材",         "HN"),
    ("C100008", "北京中科精密",         "精密仪器",     "HB"),
    ("C100009", "天津大港石化",         "石化",         "HB"),
    ("C100010", "唐山钢铁集团",         "冶金",         "HB"),
    ("C100011", "成都飞机工业",         "航空",         "HX"),
    ("C100012", "重庆长安实业",         "汽车零部件",   "HX"),
    ("C100013", "西安西电变压器",       "电气",         "XB"),
    ("C100014", "兰州石化机械",         "石化",         "XB"),
    ("C100015", "沈阳重型机床",         "重型装备",     "DB"),
    ("C100016", "大连船舶重工",         "船舶",         "DB"),
    ("C100017", "长春一汽配件",         "汽车零部件",   "DB"),
    ("C100018", "青岛海尔智家",         "家电",         "HB"),
    ("C100019", "杭州西子电梯",         "机电",         "HD"),
    ("C100020", "厦门金龙客车",         "商用车",       "HN"),
]

PLANTS = [
    ("1001", "上海工厂"),
    ("1002", "南京工厂"),
    ("2001", "广州工厂"),
    ("3001", "北京工厂"),
    ("4001", "成都工厂"),
    ("5001", "沈阳工厂"),
]

MATERIALS = [
    # (MATNR, MaterialDesc, MTART/物料类型, Unit)
    ("M001001", "高压断路器 ZN-12kV",       "FERT", "PC"),
    ("M001002", "中压开关柜 KYN28-12",      "FERT", "PC"),
    ("M001003", "干式变压器 SCB-1000kVA",   "FERT", "PC"),
    ("M001004", "油浸变压器 S11-1600kVA",   "FERT", "PC"),
    ("M002001", "工业电机 Y2-280M",         "FERT", "PC"),
    ("M002002", "伺服驱动器 SD-7.5kW",      "FERT", "PC"),
    ("M002003", "PLC 模块 S7-1500 CPU",     "FERT", "PC"),
    ("M002004", "频率变换器 G120-22kW",     "FERT", "PC"),
    ("M003001", "硅钢片 35Q155",            "ROH",  "KG"),
    ("M003002", "电解铜 Cu99.99%",          "ROH",  "KG"),
    ("M003003", "环氧树脂 EP-8001",         "ROH",  "KG"),
    ("M003004", "绝缘漆 H 级",              "ROH",  "L"),
]

ACCOUNTS = [
    # (HKONT, AccountName, AccountType)
    ("100100", "现金",          "资产"),
    ("100200", "银行存款",      "资产"),
    ("113200", "应收账款",      "资产"),
    ("122100", "存货-原材料",   "资产"),
    ("122200", "存货-在产品",   "资产"),
    ("122300", "存货-成品",     "资产"),
    ("213200", "应付账款",      "负债"),
    ("400100", "主营业务收入",  "损益"),
    ("500100", "主营业务成本",  "损益"),
    ("600100", "销售费用",      "损益"),
    ("600200", "管理费用",      "损益"),
    ("600300", "财务费用",      "损益"),
]


# ============================== 通用辅助 ==============================


def _ensure(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
    _ensure(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow(r)
    print(f"  ✓ {path.relative_to(ROOT)}  ({len(rows)} 行)")


def _money(low: float, high: float) -> float:
    """生成"万元"级金额,保留 1 位小数。"""
    return round(random.uniform(low, high), 1)


def _pct(low: float, high: float) -> float:
    return round(random.uniform(low, high), 1)


def _month_offset(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[4:])
    m += delta
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}{m:02d}"


# ============================== ZBW_SALES_SRV ==============================


def gen_sales() -> None:
    svc = SERVICES / "ZBW_SALES_SRV"

    # SalesByOfficeView: 每办事处 × 每月
    rows = []
    for off_code, off_name, region in OFFICES:
        region_name = dict(REGIONS)[region]
        base = _money(500, 3000)
        trend = random.uniform(-0.15, 0.30)
        for i, m in enumerate(MONTHS):
            netwr = round(base * (1 + trend * i / len(MONTHS)) * random.uniform(0.85, 1.15), 1)
            gp_ratio = _pct(15, 38)
            gp = round(netwr * gp_ratio / 100, 1)
            yoy = _pct(-12, 28)
            mom = _pct(-15, 22)
            rows.append([off_code, off_name, region, region_name, m, netwr, gp, gp_ratio, yoy, mom])
    _write_csv(
        svc / "data" / "SalesByOfficeView.csv",
        ["OfficeCode", "OfficeName", "Region", "RegionName", "CALMONTH",
         "NETWR_F", "GROSS_PROFIT", "GP_RATIO", "YoY", "MoM"],
        rows,
    )

    # SalesByCustomer: 客户 × 月（不是每个月都有,模拟稀疏）
    rows = []
    for kunnr, name, industry, region in CUSTOMERS:
        for m in MONTHS:
            if random.random() < 0.7:        # 70% 概率本月有订单
                netwr = _money(20, 1500)
                order_cnt = random.randint(1, 25)
                rows.append([kunnr, name, industry, region, m, netwr, order_cnt])
    _write_csv(
        svc / "data" / "SalesByCustomer.csv",
        ["KUNNR", "CustomerName", "Industry", "Region", "CALMONTH", "NETWR_F", "OrderCount"],
        rows,
    )

    # SalesOrderHeader: 订单明细
    statuses = ["完成", "完成", "完成", "履约中", "履约中", "新建", "取消"]
    rows = []
    order_seq = 1
    for m in MONTHS:
        year, month = m[:4], m[4:]
        for _ in range(random.randint(30, 60)):
            cust = random.choice(CUSTOMERS)
            office = random.choice([o for o in OFFICES if o[2] == cust[3]])
            day = random.randint(1, 28)
            vbeln = f"45{order_seq:08d}"
            order_seq += 1
            erdat = f"{year}-{month}-{day:02d}"
            netwr = _money(5, 500)
            status = random.choice(statuses)
            rows.append([vbeln, cust[0], cust[1], office[0], erdat, netwr, status])
    _write_csv(
        svc / "data" / "SalesOrderHeader.csv",
        ["VBELN", "KUNNR", "CustomerName", "OfficeCode", "ERDAT", "NETWR_F", "Status"],
        rows,
    )


# ============================== ZBW_INV_SRV ==============================


INV_META = {
    "entity_sets": [
        {
            "name": "StockByMaterial",
            "entity_type": "ZBW_INV_SRV.StockByMaterial",
            "keys": ["MATNR", "WERKS"],
            "properties": [
                {"name": "MATNR",        "type": "Edm.String",  "label": "物料编号"},
                {"name": "MaterialDesc", "type": "Edm.String",  "label": "物料描述"},
                {"name": "MTART",        "type": "Edm.String",  "label": "物料类型 (FERT成品/ROH原料)"},
                {"name": "WERKS",        "type": "Edm.String",  "label": "工厂代码"},
                {"name": "PlantName",    "type": "Edm.String",  "label": "工厂名称"},
                {"name": "STOCK_QTY",    "type": "Edm.Decimal", "label": "库存数量"},
                {"name": "MEINS",        "type": "Edm.String",  "label": "单位"},
                {"name": "STOCK_VALUE",  "type": "Edm.Decimal", "label": "库存金额(万元)"},
                {"name": "TURNOVER_DAY", "type": "Edm.Decimal", "label": "周转天数"},
                {"name": "SafetyStock",  "type": "Edm.Decimal", "label": "安全库存"}
            ]
        },
        {
            "name": "StockMovement",
            "entity_type": "ZBW_INV_SRV.StockMovement",
            "keys": ["MBLNR"],
            "properties": [
                {"name": "MBLNR",        "type": "Edm.String",  "label": "物料凭证号"},
                {"name": "BUDAT",        "type": "Edm.String",  "label": "过账日期 (YYYY-MM-DD)"},
                {"name": "WERKS",        "type": "Edm.String",  "label": "工厂代码"},
                {"name": "MATNR",        "type": "Edm.String",  "label": "物料编号"},
                {"name": "MoveType",     "type": "Edm.String",  "label": "移动类型 (101入库/261发料/501收货)"},
                {"name": "MENGE",        "type": "Edm.Decimal", "label": "数量"},
                {"name": "DMBTR",        "type": "Edm.Decimal", "label": "金额(万元)"}
            ]
        }
    ]
}


def gen_inv() -> None:
    svc = SERVICES / "ZBW_INV_SRV"
    _ensure(svc)
    (svc / "meta.json").write_text(__import__("json").dumps(INV_META, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {(svc / 'meta.json').relative_to(ROOT)}")

    # StockByMaterial: 物料 × 工厂
    rows = []
    for matnr, desc, mtart, unit in MATERIALS:
        for werks, plant_name in PLANTS:
            if random.random() < 0.65:                  # 不是每个工厂都有
                qty = round(random.uniform(50, 5000), 0)
                value = round(qty * random.uniform(0.01, 5.0), 1)
                turnover = round(random.uniform(15, 180), 1)
                safety = round(qty * random.uniform(0.1, 0.3), 0)
                rows.append([matnr, desc, mtart, werks, plant_name, qty, unit, value, turnover, safety])
    _write_csv(
        svc / "data" / "StockByMaterial.csv",
        ["MATNR", "MaterialDesc", "MTART", "WERKS", "PlantName",
         "STOCK_QTY", "MEINS", "STOCK_VALUE", "TURNOVER_DAY", "SafetyStock"],
        rows,
    )

    # StockMovement: 物料移动凭证
    move_types = ["101", "101", "261", "261", "261", "501", "601"]
    rows = []
    seq = 1
    for m in MONTHS:
        year, month = m[:4], m[4:]
        for _ in range(random.randint(40, 80)):
            day = random.randint(1, 28)
            mat = random.choice(MATERIALS)
            werks = random.choice(PLANTS)[0]
            mt = random.choice(move_types)
            qty = round(random.uniform(10, 500), 0)
            dmbtr = round(qty * random.uniform(0.01, 3.0), 1)
            rows.append([f"50{seq:08d}", f"{year}-{month}-{day:02d}", werks, mat[0], mt, qty, dmbtr])
            seq += 1
    _write_csv(
        svc / "data" / "StockMovement.csv",
        ["MBLNR", "BUDAT", "WERKS", "MATNR", "MoveType", "MENGE", "DMBTR"],
        rows,
    )


# ============================== ZBW_FIN_SRV ==============================


FIN_META = {
    "entity_sets": [
        {
            "name": "GLBalance",
            "entity_type": "ZBW_FIN_SRV.GLBalance",
            "keys": ["HKONT", "CALMONTH", "BUKRS"],
            "properties": [
                {"name": "BUKRS",        "type": "Edm.String",  "label": "公司代码"},
                {"name": "HKONT",        "type": "Edm.String",  "label": "总账科目"},
                {"name": "AccountName",  "type": "Edm.String",  "label": "科目名称"},
                {"name": "AccountType",  "type": "Edm.String",  "label": "科目类型 (资产/负债/损益)"},
                {"name": "CALMONTH",     "type": "Edm.String",  "label": "年月"},
                {"name": "DEBIT_F",      "type": "Edm.Decimal", "label": "借方发生额(万元)"},
                {"name": "CREDIT_F",     "type": "Edm.Decimal", "label": "贷方发生额(万元)"},
                {"name": "BALANCE_F",    "type": "Edm.Decimal", "label": "期末余额(万元)"}
            ]
        },
        {
            "name": "ARAging",
            "entity_type": "ZBW_FIN_SRV.ARAging",
            "keys": ["KUNNR"],
            "properties": [
                {"name": "KUNNR",        "type": "Edm.String",  "label": "客户编号"},
                {"name": "CustomerName", "type": "Edm.String",  "label": "客户名称"},
                {"name": "TOTAL_F",      "type": "Edm.Decimal", "label": "应收总额(万元)"},
                {"name": "Bucket_0_30",  "type": "Edm.Decimal", "label": "0-30天(万元)"},
                {"name": "Bucket_31_60", "type": "Edm.Decimal", "label": "31-60天(万元)"},
                {"name": "Bucket_61_90", "type": "Edm.Decimal", "label": "61-90天(万元)"},
                {"name": "Bucket_90Plus","type": "Edm.Decimal", "label": "90天以上(万元)"}
            ]
        },
        {
            "name": "APAging",
            "entity_type": "ZBW_FIN_SRV.APAging",
            "keys": ["LIFNR"],
            "properties": [
                {"name": "LIFNR",         "type": "Edm.String",  "label": "供应商编号"},
                {"name": "SupplierName",  "type": "Edm.String",  "label": "供应商名称"},
                {"name": "TOTAL_F",       "type": "Edm.Decimal", "label": "应付总额(万元)"},
                {"name": "Bucket_0_30",   "type": "Edm.Decimal", "label": "0-30天(万元)"},
                {"name": "Bucket_31_60",  "type": "Edm.Decimal", "label": "31-60天(万元)"},
                {"name": "Bucket_61_90",  "type": "Edm.Decimal", "label": "61-90天(万元)"},
                {"name": "Bucket_90Plus", "type": "Edm.Decimal", "label": "90天以上(万元)"}
            ]
        }
    ]
}


def gen_fin() -> None:
    svc = SERVICES / "ZBW_FIN_SRV"
    _ensure(svc)
    (svc / "meta.json").write_text(__import__("json").dumps(FIN_META, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {(svc / 'meta.json').relative_to(ROOT)}")

    # GLBalance: 科目 × 月（公司代码 1000）
    rows = []
    for hkont, name, atype in ACCOUNTS:
        running = _money(500, 5000)
        for m in MONTHS:
            debit  = _money(50, 800)
            credit = _money(50, 800)
            running = round(running + debit - credit, 1)
            rows.append(["1000", hkont, name, atype, m, debit, credit, running])
    _write_csv(
        svc / "data" / "GLBalance.csv",
        ["BUKRS", "HKONT", "AccountName", "AccountType", "CALMONTH",
         "DEBIT_F", "CREDIT_F", "BALANCE_F"],
        rows,
    )

    # ARAging: 每客户一行
    rows = []
    for kunnr, name, *_ in CUSTOMERS:
        a, b, c, d = _money(20, 800), _money(0, 300), _money(0, 150), _money(0, 80)
        total = round(a + b + c + d, 1)
        rows.append([kunnr, name, total, a, b, c, d])
    _write_csv(
        svc / "data" / "ARAging.csv",
        ["KUNNR", "CustomerName", "TOTAL_F", "Bucket_0_30", "Bucket_31_60", "Bucket_61_90", "Bucket_90Plus"],
        rows,
    )

    # APAging: 模拟供应商
    suppliers = [
        ("S200001", "宝钢股份"),
        ("S200002", "中国铝业"),
        ("S200003", "金风科技"),
        ("S200004", "海螺水泥"),
        ("S200005", "万华化学"),
        ("S200006", "三花智控"),
        ("S200007", "汇川技术"),
        ("S200008", "宁德时代"),
        ("S200009", "立讯精密"),
        ("S200010", "京东方"),
    ]
    rows = []
    for lifnr, name in suppliers:
        a, b, c, d = _money(30, 1000), _money(0, 400), _money(0, 200), _money(0, 100)
        total = round(a + b + c + d, 1)
        rows.append([lifnr, name, total, a, b, c, d])
    _write_csv(
        svc / "data" / "APAging.csv",
        ["LIFNR", "SupplierName", "TOTAL_F", "Bucket_0_30", "Bucket_31_60", "Bucket_61_90", "Bucket_90Plus"],
        rows,
    )


# ============================== ZBW_PROD_SRV ==============================


PROD_META = {
    "entity_sets": [
        {
            "name": "ProductionOrder",
            "entity_type": "ZBW_PROD_SRV.ProductionOrder",
            "keys": ["AUFNR"],
            "properties": [
                {"name": "AUFNR",         "type": "Edm.String",  "label": "生产订单号"},
                {"name": "WERKS",         "type": "Edm.String",  "label": "工厂代码"},
                {"name": "PlantName",     "type": "Edm.String",  "label": "工厂名称"},
                {"name": "MATNR",         "type": "Edm.String",  "label": "物料编号"},
                {"name": "MaterialDesc",  "type": "Edm.String",  "label": "物料描述"},
                {"name": "PSTRT",         "type": "Edm.String",  "label": "计划开始日期"},
                {"name": "PEND",          "type": "Edm.String",  "label": "计划结束日期"},
                {"name": "PLAN_QTY",      "type": "Edm.Decimal", "label": "计划数量"},
                {"name": "ACTUAL_QTY",    "type": "Edm.Decimal", "label": "实际数量"},
                {"name": "Status",        "type": "Edm.String",  "label": "状态 (REL已下达/CNF已确认/TECO技术完成)"}
            ]
        },
        {
            "name": "YieldByPlant",
            "entity_type": "ZBW_PROD_SRV.YieldByPlant",
            "keys": ["WERKS", "CALMONTH"],
            "properties": [
                {"name": "WERKS",        "type": "Edm.String",  "label": "工厂代码"},
                {"name": "PlantName",    "type": "Edm.String",  "label": "工厂名称"},
                {"name": "CALMONTH",     "type": "Edm.String",  "label": "年月"},
                {"name": "OUTPUT_QTY",   "type": "Edm.Decimal", "label": "产量"},
                {"name": "DEFECT_QTY",   "type": "Edm.Decimal", "label": "不良数量"},
                {"name": "YIELD_RATE",   "type": "Edm.Decimal", "label": "良率(%)"},
                {"name": "OEE",          "type": "Edm.Decimal", "label": "OEE(%)"}
            ]
        }
    ]
}


def gen_prod() -> None:
    svc = SERVICES / "ZBW_PROD_SRV"
    _ensure(svc)
    (svc / "meta.json").write_text(__import__("json").dumps(PROD_META, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {(svc / 'meta.json').relative_to(ROOT)}")

    # ProductionOrder: ~100 单
    statuses = ["CNF", "CNF", "CNF", "REL", "REL", "TECO"]
    finished_mats = [m for m in MATERIALS if m[2] == "FERT"]
    rows = []
    seq = 1
    for m in MONTHS:
        year, month = m[:4], m[4:]
        for _ in range(random.randint(12, 22)):
            werks, plant = random.choice(PLANTS)
            mat = random.choice(finished_mats)
            start_day = random.randint(1, 20)
            end_day = min(28, start_day + random.randint(3, 14))
            plan = round(random.uniform(50, 500), 0)
            status = random.choice(statuses)
            actual = round(plan * random.uniform(0.85, 1.05), 0) if status != "REL" else 0
            rows.append([
                f"AU{seq:08d}", werks, plant, mat[0], mat[1],
                f"{year}-{month}-{start_day:02d}",
                f"{year}-{month}-{end_day:02d}",
                plan, actual, status,
            ])
            seq += 1
    _write_csv(
        svc / "data" / "ProductionOrder.csv",
        ["AUFNR", "WERKS", "PlantName", "MATNR", "MaterialDesc",
         "PSTRT", "PEND", "PLAN_QTY", "ACTUAL_QTY", "Status"],
        rows,
    )

    # YieldByPlant: 工厂 × 月
    rows = []
    for werks, plant in PLANTS:
        for m in MONTHS:
            output = round(random.uniform(800, 8000), 0)
            defect = round(output * random.uniform(0.005, 0.08), 0)
            yield_rate = round((output - defect) / output * 100, 2)
            oee = round(random.uniform(60, 92), 1)
            rows.append([werks, plant, m, output, defect, yield_rate, oee])
    _write_csv(
        svc / "data" / "YieldByPlant.csv",
        ["WERKS", "PlantName", "CALMONTH", "OUTPUT_QTY", "DEFECT_QTY", "YIELD_RATE", "OEE"],
        rows,
    )


# ============================== ZBW_PROC_SRV ==============================


PROC_META = {
    "entity_sets": [
        {
            "name": "PurchaseOrder",
            "entity_type": "ZBW_PROC_SRV.PurchaseOrder",
            "keys": ["EBELN"],
            "properties": [
                {"name": "EBELN",         "type": "Edm.String",  "label": "采购订单号"},
                {"name": "LIFNR",         "type": "Edm.String",  "label": "供应商编号"},
                {"name": "SupplierName",  "type": "Edm.String",  "label": "供应商名称"},
                {"name": "WERKS",         "type": "Edm.String",  "label": "工厂代码"},
                {"name": "MATNR",         "type": "Edm.String",  "label": "物料编号"},
                {"name": "MaterialDesc",  "type": "Edm.String",  "label": "物料描述"},
                {"name": "BEDAT",         "type": "Edm.String",  "label": "采购日期"},
                {"name": "EINDT",         "type": "Edm.String",  "label": "交付日期"},
                {"name": "MENGE",         "type": "Edm.Decimal", "label": "数量"},
                {"name": "NETWR_F",       "type": "Edm.Decimal", "label": "金额(万元)"},
                {"name": "DeliveryStatus","type": "Edm.String",  "label": "交付状态 (已交付/逾期/在途)"}
            ]
        }
    ]
}


def gen_proc() -> None:
    svc = SERVICES / "ZBW_PROC_SRV"
    _ensure(svc)
    (svc / "meta.json").write_text(__import__("json").dumps(PROC_META, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {(svc / 'meta.json').relative_to(ROOT)}")

    suppliers = [
        ("S200001", "宝钢股份"), ("S200002", "中国铝业"), ("S200003", "金风科技"),
        ("S200004", "海螺水泥"), ("S200005", "万华化学"), ("S200006", "三花智控"),
        ("S200007", "汇川技术"), ("S200008", "宁德时代"), ("S200009", "立讯精密"),
        ("S200010", "京东方"),
    ]
    raw_mats = [m for m in MATERIALS if m[2] == "ROH"]
    status_pool = ["已交付", "已交付", "已交付", "在途", "逾期"]

    rows = []
    seq = 1
    for m in MONTHS:
        year, month = m[:4], m[4:]
        for _ in range(random.randint(20, 35)):
            sup = random.choice(suppliers)
            werks = random.choice(PLANTS)[0]
            mat = random.choice(raw_mats)
            bedat_day = random.randint(1, 20)
            eindt_day = min(28, bedat_day + random.randint(7, 25))
            menge = round(random.uniform(100, 10000), 0)
            netwr = _money(5, 300)
            status = random.choice(status_pool)
            rows.append([
                f"45{seq:08d}", sup[0], sup[1], werks, mat[0], mat[1],
                f"{year}-{month}-{bedat_day:02d}",
                f"{year}-{month}-{eindt_day:02d}",
                menge, netwr, status,
            ])
            seq += 1
    _write_csv(
        svc / "data" / "PurchaseOrder.csv",
        ["EBELN", "LIFNR", "SupplierName", "WERKS", "MATNR", "MaterialDesc",
         "BEDAT", "EINDT", "MENGE", "NETWR_F", "DeliveryStatus"],
        rows,
    )


# ============================== 入口 ==============================


def main() -> None:
    print(f"生成 Mock 数据 -> {SERVICES}")
    gen_sales()
    gen_inv()
    gen_fin()
    gen_prod()
    gen_proc()
    print("完成。")


if __name__ == "__main__":
    main()
