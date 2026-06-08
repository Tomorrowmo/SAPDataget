#!/usr/bin/env python
"""SAP OData 探针 —— 在【有 SAP 的环境】里跑一次,自动捕获诊断对接所需的真实信息,
写成 tools/sap_probe_report.md,供带回给开发分析(原则:不臆造,一切以真实响应为准)。

它只做**只读 GET**(小 $top),安全。捕获内容:
  1. 报告清单(ZBW_QUERY_LIST_SRV/LtResultSet)原始响应:字段名、<m:count> 总数、单页返回了多少条、
     是否有分页 next 链接 —— 解释"8506 / 200 / 0"那个现象的关键。
  2. 该服务 $metadata:每个字段的 Edm 类型 + 主键,定位"归属用户"字段的真实名字。
  3. 服务端按用户过滤是否生效:对候选归属字段试 $filter=<字段> eq '你的用户',看是否成功 + 你的真实条数。
  4. 分页是否生效:用客户端 execute_query(top=1000) 看能不能跟 __next 拿到多页。
  5. SAP 错误体格式:故意查不存在的字段,抓真实报错结构(用于让 Agent 能精准自纠)。
  6. 目录服务:列前 10 个 OData 服务。
  7. (可选)--service/--entityset 探任意服务,抓真实 Decimal/DateTime 编码(验证类型转换)。

用法(在仓库根目录):
  python tools/sap_probe.py --user 你的SAP用户名
  # 密码:交互输入(getpass);或 --password / 环境变量 BW_PASSWORD
  # base_url / client 默认读 .env;也可 --base-url / --client 覆盖
  # 探业务服务: python tools/sap_probe.py --user U --service ZBW_SALES_SRV --entityset SalesByOfficeView

⚠ 报告里会包含少量样本数据值。发我之前请快速过一眼,把任何敏感业务数据打码。
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

# 让脚本在仓库根目录下能 import app
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from app import odata  # noqa: E402
from app.bw.live import LiveBWClient  # noqa: E402
from app.config import BWSettings  # noqa: E402

load_dotenv(ROOT / ".env")

REPORT_SERVICE = os.environ.get("REPORT_LIST_SERVICE", "ZBW_QUERY_LIST_SRV")
REPORT_ENTITYSET = os.environ.get("REPORT_LIST_ENTITY_SET", "LtResultSet")
OWNER_FIELD = os.environ.get("OWNER_FIELD", "UName")

_OUT: list[str] = []


def w(line: str = "") -> None:
    _OUT.append(line)
    print(line)


def section(title: str) -> None:
    w("")
    w("## " + title)


def code(obj, limit: int = 3000) -> None:
    if not isinstance(obj, str):
        obj = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    if len(obj) > limit:
        obj = obj[:limit] + f"\n…(截断,原长 {len(obj)})"
    w("```")
    w(obj)
    w("```")


def _read_password(provided: str) -> str:
    """取密码:--password / 环境变量 BW_PASSWORD / 交互输入。
    getpass 在部分终端(Git Bash、某些 IDE 终端)不可用或卡住 → 自动退回可见的 input()。"""
    if provided:
        return provided
    env = os.environ.get("BW_PASSWORD")
    if env:
        return env
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return getpass.getpass("SAP 密码(输入时不显示,直接打完按回车): ")
    except Exception:                                              # noqa: BLE001
        pass
    print("(当前终端无法隐藏输入,密码将明文可见;或改用 --password / .env 的 BW_PASSWORD)")
    return input("SAP 密码: ")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _owner_candidates(field_names: list[str]) -> list[str]:
    """从字段名里挑"可能是归属用户"的候选:配置的 OWNER_FIELD 优先,再加含 user/unam/ernam/usnam 的。"""
    cands: list[str] = []
    for f in field_names:
        fl = f.lower()
        if fl == OWNER_FIELD.lower() or any(k in fl for k in ("uname", "user", "ernam", "usnam", " accnt")):
            if f not in cands:
                cands.append(f)
    # OWNER_FIELD 放最前
    cands.sort(key=lambda x: 0 if x.lower() == OWNER_FIELD.lower() else 1)
    return cands


def main() -> None:
    ap = argparse.ArgumentParser(description="SAP OData 探针")
    ap.add_argument("--user", default=os.environ.get("BW_USERNAME", ""), help="SAP 用户名")
    ap.add_argument("--password", default=os.environ.get("BW_PASSWORD", ""), help="SAP 密码(不传则交互输入)")
    ap.add_argument("--base-url", default=os.environ.get("BW_BASE_URL", ""), help="SAP base url(默认读 .env)")
    ap.add_argument("--client", default=os.environ.get("BW_CLIENT", ""), help="sap-client(默认读 .env)")
    ap.add_argument("--language", default=os.environ.get("BW_LANGUAGE", "EN"))
    ap.add_argument("--owner-value", default="", help="按此用户值测试过滤(默认=登录名大写)")
    ap.add_argument("--service", default="", help="额外探一个业务服务")
    ap.add_argument("--entityset", default="", help="该业务服务的 EntitySet")
    ap.add_argument("--no-verify-ssl", action="store_true")
    args = ap.parse_args()

    user = args.user.strip()
    if not user:
        user = input("SAP 用户名: ").strip()
    password = _read_password(args.password)
    base_url = (args.base_url or "").rstrip("/")
    if not base_url:
        print("缺 base_url(请在 .env 配 BW_BASE_URL 或用 --base-url)"); sys.exit(1)
    owner_value = (args.owner_value or user).upper()

    settings = BWSettings(
        mode="live", mock_data_dir=ROOT / "mock_data", mock_latency_ms=0,
        base_url=base_url, username=user, password=password,
        client=args.client, language=args.language,
        verify_ssl=not args.no_verify_ssl,
        timeout=int(os.environ.get("BW_TIMEOUT", "60")),
        client_fallback=True, max_export_rows=50000,
    )
    client = LiveBWClient(settings)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    w(f"# SAP OData 探针报告")
    w(f"- 时间: {stamp}")
    w(f"- 主机: {base_url}  client={args.client or '(空)'}  user={user}  ssl_verify={not args.no_verify_ssl}")
    w(f"- 报告清单服务: {REPORT_SERVICE}/{REPORT_ENTITYSET}  | 配置归属字段 OWNER_FIELD={OWNER_FIELD}")
    w("")
    w("> ⚠ 含少量样本值,发出前请打码敏感业务数据。")

    report_url = f"{base_url}/sap/opu/odata/sap/{REPORT_SERVICE}/{REPORT_ENTITYSET}"

    # ---------- 1) 报告清单 原始响应(XML,和 app 固定 URL 一致:不带 $top) ----------
    section("1) 报告清单原始响应(不带 $top,看默认单页返回多少 + 总数 + 是否分页)")
    field_names: list[str] = []
    try:
        params = {}
        if args.client:
            params["sap-client"] = args.client
        if args.language:
            params["sap-language"] = args.language
        r = requests.get(report_url, auth=(user, password),
                         headers={"Accept": "application/atom+xml,application/xml,text/xml"},
                         params=params, timeout=settings.timeout, verify=settings.verify_ssl)
        w(f"- HTTP {r.status_code}, Content-Type: {r.headers.get('content-type')}")
        count = None
        entry_n = 0
        next_url = None
        try:
            root = ET.fromstring(r.text)
            for node in root.iter():
                loc = _local(node.tag)
                if loc == "count" and count is None:
                    count = (node.text or "").strip()
                if loc == "link" and node.attrib.get("rel") == "next":
                    next_url = node.attrib.get("href")
                if loc == "entry":
                    entry_n += 1
                    if not field_names:
                        for ch in node.iter():
                            if _local(ch.tag) == "properties":
                                field_names = [_local(p.tag) for p in list(ch)]
                                break
        except ET.ParseError as e:
            w(f"- (XML 解析失败: {e})")
        w(f"- <m:count> 总数 = **{count}**  | 本次响应实际 entry 条数 = **{entry_n}**  | 分页 next 链接 = {next_url or '无'}")
        w(f"- 字段名: {field_names}")
        w("原始响应片段:")
        code(r.text, 2000)
    except Exception as e:                                          # noqa: BLE001
        w(f"- 失败: {type(e).__name__}: {e}")

    # ---------- 2) $metadata: 字段 Edm 类型 + 主键 + 归属字段 ----------
    section("2) 报告清单 $metadata(字段 Edm 类型 + 主键 → 定位归属用户字段)")
    try:
        meta = client.get_metadata(REPORT_SERVICE)
        if meta.error:
            w(f"- 失败: {meta.error}")
        else:
            for es in (meta.json or {}).get("entity_sets", []):
                if es.get("name") == REPORT_ENTITYSET:
                    w(f"- EntitySet {es['name']} 主键: {es.get('keys')}")
                    for p in es.get("properties", []):
                        w(f"  - {p.get('name')}  ({p.get('type')})  label={p.get('label')}")
                    if not field_names:
                        field_names = [p.get("name") for p in es.get("properties", [])]
    except Exception as e:                                          # noqa: BLE001
        w(f"- 失败: {type(e).__name__}: {e}")

    # ---------- 3) 服务端按用户过滤是否生效 ----------
    section("3) 服务端按用户过滤是否生效($filter=<归属字段> eq '你的用户大写')")
    cands = _owner_candidates(field_names) or [OWNER_FIELD]
    w(f"- 候选归属字段: {cands}  | 用户值(大写): '{owner_value}'")
    for field in cands:
        try:
            flt = odata.build_filter([{"field": field, "op": "eq", "value": owner_value}],
                                     {field: "Edm.String"})
            resp = client.execute_query(REPORT_SERVICE, REPORT_ENTITYSET, filter=flt, top=5, count=True)
            if resp.error:
                w(f"  - `{field}`: ❌ {resp.error}")
            else:
                j = resp.json or {}
                w(f"  - `{field}`: ✅ 成功  你的条数(__count)={j.get('row_count_total')}  本页样本={j.get('row_count_returned')}")
                sample = (j.get("rows") or [])[:2]
                if sample:
                    code(sample, 1200)
        except Exception as e:                                     # noqa: BLE001
            w(f"  - `{field}`: 异常 {type(e).__name__}: {e}")

    # ---------- 4) 分页是否生效(客户端 top=1000) ----------
    section("4) 分页是否生效(execute_query top=1000,看能否跟 __next 拿多页)")
    try:
        resp = client.execute_query(REPORT_SERVICE, REPORT_ENTITYSET, top=1000, count=True)
        if resp.error:
            w(f"- 失败: {resp.error}")
        else:
            j = resp.json or {}
            w(f"- 拿回 row_count_returned={j.get('row_count_returned')}  | row_count_total={j.get('row_count_total')}")
            w("- 若 returned 远大于单页(第1节的 entry 条数),说明分页生效;若 ≈ 单页,说明该服务未给 __next。")
    except Exception as e:                                          # noqa: BLE001
        w(f"- 失败: {type(e).__name__}: {e}")

    # ---------- 5) SAP 错误体格式 ----------
    section("5) SAP 错误体格式(查一个不存在的字段,抓真实报错结构)")
    try:
        resp = client.execute_query(REPORT_SERVICE, REPORT_ENTITYSET,
                                    filter="ZZ_NoSuchField eq 'x'", top=1)
        w(f"- 客户端归一化后的 error = {resp.error!r}")
        if resp.text:
            w("- 原始错误体片段:")
            code(resp.text, 1500)
    except Exception as e:                                          # noqa: BLE001
        w(f"- 失败: {type(e).__name__}: {e}")

    # ---------- 6) 目录服务 ----------
    section("6) 目录服务(前 10 个 OData 服务)")
    try:
        resp = client.list_services(top=10)
        if resp.error:
            w(f"- 失败: {resp.error}")
        else:
            for s in (resp.json or {}).get("services", [])[:10]:
                w(f"  - {s.get('TechnicalServiceName')}  | {s.get('Title')}")
    except Exception as e:                                          # noqa: BLE001
        w(f"- 失败: {type(e).__name__}: {e}")

    # ---------- 7) 可选:任意业务服务(看真实 Decimal/DateTime 编码) ----------
    if args.service and args.entityset:
        section(f"7) 业务服务原始编码 {args.service}/{args.entityset}(看 Decimal/DateTime 真实编码)")
        try:
            url = f"{base_url}/sap/opu/odata/sap/{args.service.strip('/')}/{args.entityset.strip('/')}"
            params = {"$format": "json", "$top": "2"}
            if args.client:
                params["sap-client"] = args.client
            if args.language:
                params["sap-language"] = args.language
            r = requests.get(url, auth=(user, password), headers={"Accept": "application/json"},
                            params=params, timeout=settings.timeout, verify=settings.verify_ssl)
            w(f"- HTTP {r.status_code}")
            w("- 原始 JSON(注意金额是否为字符串、日期是否 /Date(ms)/):")
            code(r.text, 2500)
            meta = client.get_metadata(args.service)
            if not meta.error:
                for es in (meta.json or {}).get("entity_sets", []):
                    if es.get("name") == args.entityset:
                        w("- 该 EntitySet 字段类型:")
                        for p in es.get("properties", []):
                            w(f"  - {p.get('name')} ({p.get('type')})")
        except Exception as e:                                     # noqa: BLE001
            w(f"- 失败: {type(e).__name__}: {e}")

    # ---------- 写文件 ----------
    out_path = ROOT / "tools" / "sap_probe_report.md"
    out_path.write_text("\n".join(_OUT), encoding="utf-8")
    print("")
    print(f"✅ 报告已写入: {out_path}")
    print("把这个文件的内容贴给开发即可(发前请打码敏感数据)。")


if __name__ == "__main__":
    main()
