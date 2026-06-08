"""LiveBWClient —— 真实 SAP BW 7.5 NetWeaver Gateway 客户端。

封装内容（与 §8.3 一致）:
  * HTTP Basic Auth + sap-client + sap-language
  * 服务目录 /sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection
  * $metadata 拉取 + EDMX 简化解析（去掉噪声字段,只保留 entity_sets/properties）
  * 通用 OData V2 查询执行
  * 错误归一化为 ODataResponse
"""
from __future__ import annotations

import logging
from typing import Any
from xml.etree import ElementTree as ET

import requests
import urllib3
from requests.auth import HTTPBasicAuth

from app import odata
from app.bw.interface import BWClient, ODataResponse
from app.config import BWSettings

log = logging.getLogger(__name__)

CATALOG_PATH = "/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection"
ODATA_SERVICE_ROOT = "/sap/opu/odata/sap"
_MAX_PAGES = 200                 # 分页跟随 __next 的硬上限(配合 settings.max_export_rows)


class LiveBWClient(BWClient):
    """真实 BW 客户端 —— HTTP 走 SAP NetWeaver Gateway。"""

    def __init__(self, settings: BWSettings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(settings.username, settings.password)
        self.session.headers.update({
            "Accept": "application/json",
            "Accept-Language": settings.language,
        })
        if not settings.verify_ssl:
            self.session.verify = False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # $metadata 简化结果缓存(key=service),供列 label + Edm 类型转换复用,避免每查询重拉。
        self._meta_cache: dict[str, dict[str, Any]] = {}

    # ---------- 基础请求 ----------
    def _default_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.settings.client:
            params["sap-client"] = self.settings.client
        if self.settings.language:
            params["sap-language"] = self.settings.language
        return params

    def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        accept_json: bool = True,
    ) -> requests.Response:
        url = path if path.startswith("http") else self.settings.base_url + path
        merged = self._default_params()
        if params:
            merged.update({k: v for k, v in params.items() if v is not None})
        headers = {}
        if not accept_json:
            headers["Accept"] = "application/xml"
        resp = self.session.get(
            url,
            params=merged,
            headers=headers,
            timeout=self.settings.timeout,
        )
        # 某些系统不接受显式 sap-client → 仅在 401/403(鉴权类)时回退一次为不带 client。
        # 不再对 404 回退(404 是资源不存在,与 client 无关);回退会落到 Gateway 默认
        # client,可能读到另一个 client 的数据,因此必须留痕,且可用 BW_CLIENT_FALLBACK 关闭。
        if (
            self.settings.client_fallback
            and "sap-client" in merged
            and resp.status_code in (401, 403)
        ):
            log.warning(
                "sap-client=%s 在 %s 上返回 %s,回退为不带 sap-client 重试一次"
                "(可能落到 Gateway 默认 client,请确认数据归属)。",
                merged.get("sap-client"), url, resp.status_code,
            )
            fallback_params = {k: v for k, v in merged.items() if k != "sap-client"}
            return self.session.get(
                url,
                params=fallback_params,
                headers=headers,
                timeout=self.settings.timeout,
            )
        return resp

    def _get_url(self, url: str) -> requests.Response:
        """裸 GET 一个完整 URL(用于跟随 OData __next 分页链接)。__next 已自带 query
        与 sap-client 上下文,这里不再叠加默认参数,避免重复 sap-client。"""
        full = url if url.startswith("http") else self.settings.base_url + url
        return self.session.get(full, timeout=self.settings.timeout)

    # ---------- BWClient 接口实现 ----------
    def describe(self) -> str:
        return f"LiveBWClient(base_url={self.settings.base_url}, client={self.settings.client})"

    def list_services(self, search: str | None = None, top: int = 50) -> ODataResponse:
        params = {"$format": "json", "$top": str(top)}
        if search:
            params["$filter"] = (
                f"substringof('{search}',Title) "
                f"or substringof('{search}',TechnicalServiceName)"
            )
        try:
            r = self._get(CATALOG_PATH, params=params)
        except requests.RequestException as e:
            return ODataResponse(0, CATALOG_PATH, error=f"请求异常: {e}")

        body: Any = None
        try:
            body = r.json()
        except ValueError:
            pass

        simplified: list[dict[str, Any]] = []
        if isinstance(body, dict):
            for s in body.get("d", {}).get("results", []) or []:
                simplified.append({
                    "TechnicalServiceName": s.get("TechnicalServiceName"),
                    "Title": s.get("Title"),
                    "Description": s.get("Description"),
                    "Version": s.get("Version"),
                    "ServiceUrl": s.get("ServiceUrl"),
                })

        return ODataResponse(
            status_code=r.status_code,
            url=r.url,
            json={"services": simplified, "count": len(simplified)},
            text=r.text if not simplified else "",
            error=None if r.ok else f"HTTP {r.status_code}",
        )

    def get_metadata(self, service: str) -> ODataResponse:
        key = service.strip("/")
        if key in self._meta_cache:
            url = f"{self.settings.base_url}{ODATA_SERVICE_ROOT}/{key}/$metadata"
            return ODataResponse(200, url, json=self._meta_cache[key])
        path = f"{ODATA_SERVICE_ROOT}/{key}/$metadata"
        try:
            r = self._get(path, accept_json=False)
        except requests.RequestException as e:
            return ODataResponse(0, path, error=f"请求异常: {e}")
        if not r.ok:
            return ODataResponse(
                r.status_code, r.url,
                text=r.text[:2000],
                error=odata.parse_odata_error(r.text, r.headers.get("content-type", "")) or f"HTTP {r.status_code}",
            )
        try:
            simplified = _parse_metadata(r.text)
        except Exception as e:
            return ODataResponse(
                r.status_code, r.url,
                text=r.text[:2000],
                error=f"$metadata 解析失败: {e}",
            )
        self._meta_cache[key] = simplified
        return ODataResponse(r.status_code, r.url, json=simplified)

    def _cached_metadata(self, service: str) -> dict[str, Any]:
        """取简化 metadata(命中缓存直接用;未命中懒拉一次)。失败返回 {}。"""
        key = service.strip("/")
        if key not in self._meta_cache:
            resp = self.get_metadata(key)
            if resp.error or not isinstance(resp.json, dict):
                return {}
        return self._meta_cache.get(key, {})

    def execute_query(
        self,
        service: str,
        entity_set: str,
        *,
        filter: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        top: int | None = 100,
        skip: int | None = None,
        expand: str | None = None,
        apply: str | None = None,
        count: bool = False,
    ) -> ODataResponse:
        path = f"{ODATA_SERVICE_ROOT}/{service.strip('/')}/{entity_set.strip('/')}"
        params: dict[str, str] = {"$format": "json"}
        if filter:
            params["$filter"] = filter
        if select:
            params["$select"] = select
        if orderby:
            params["$orderby"] = orderby
        if top is not None:
            params["$top"] = str(top)
        if skip is not None:
            params["$skip"] = str(skip)
        if expand:
            params["$expand"] = expand
        if apply:
            params["$apply"] = apply
        if count:
            params["$inlinecount"] = "allpages"

        try:
            r = self._get(path, params=params)
        except requests.RequestException as e:
            return ODataResponse(0, path, error=f"请求异常: {e}")

        first_url = r.url
        if not r.ok:
            # 解析 SAP 结构化错误体(error.message.value / <error><message>),让 Agent 能精准自纠。
            msg = odata.parse_odata_error(r.text, r.headers.get("content-type", "")) or f"HTTP {r.status_code}"
            return ODataResponse(r.status_code, first_url, text=r.text[:2000], error=msg)

        parsed = self._parse_page(r)
        if parsed is None:
            return ODataResponse(r.status_code, first_url, text=r.text[:2000],
                                 error="无法解析响应(非 OData V2 JSON/Atom)")
        rows, total, next_url = parsed

        # 跟随 __next 分页,直到取满目标行数(top)或到达安全上限。target=None 表示不限(由
        # max_export_rows 兜底)。这修复了"导出全部却只拿一页/被截断"的问题。
        target = top if top is not None else self.settings.max_export_rows
        target = min(target, self.settings.max_export_rows)
        pages = 1
        while next_url and len(rows) < target and pages < _MAX_PAGES:
            try:
                rn = self._get_url(next_url)
            except requests.RequestException:
                break
            if not rn.ok:
                break
            pnext = self._parse_page(rn)
            if pnext is None:
                break
            more, total_n, next_url = pnext
            if total is None:
                total = total_n
            rows.extend(more)
            pages += 1
        rows = rows[:target]

        # Edm 类型转换:把 Decimal 字符串/`/Date(ms)/` 等转真实类型,下游 Excel/统计/图表才正确。
        types = odata.prop_types(self._cached_metadata(service), entity_set.strip("/"))
        rows = odata.coerce_rows(rows, types)

        return ODataResponse(
            r.status_code, first_url,
            json={
                "rows": rows,
                "row_count_returned": len(rows),
                "row_count_total": total,
            },
        )

    # ---------- 单页解析(V2 d.results / V4 value / Atom XML) ----------
    def _parse_page(self, r: requests.Response) -> tuple[list[dict[str, Any]], Any, str | None] | None:
        """解析一页响应为 (rows, total, next_url)。无法解析返回 None。

        rows 已剥除 __metadata 噪声,但**不截断**(分页/导出需要完整)。
        next_url 来自 V2 ``d.__next`` 或 V4 ``@odata.nextLink`` 或 Atom ``<link rel=next>``。
        """
        body: Any = None
        try:
            body = r.json()
        except ValueError:
            body = None

        if isinstance(body, dict) and "d" in body:                    # OData V2 JSON
            d = body["d"]
            if isinstance(d, dict) and "results" in d:
                rows = _strip_metadata(d.get("results", []) or [])
                return rows, d.get("__count"), d.get("__next")
            return _strip_metadata(d if isinstance(d, list) else [d]), None, None

        if isinstance(body, dict) and isinstance(body.get("value"), list):  # OData V4 JSON
            rows = _strip_metadata(body.get("value", []) or [])
            total = body.get("@odata.count") or body.get("count")
            return rows, total, body.get("@odata.nextLink")

        parsed_xml = _parse_odata_xml_rows(r.text)                    # Atom/XML
        if parsed_xml is not None:
            return (
                parsed_xml.get("rows", []),
                parsed_xml.get("row_count_total"),
                parsed_xml.get("next_url"),
            )
        return None


# ============================== EDMX 解析 ==============================


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_metadata(xml_text: str) -> dict[str, Any]:
    """把 EDMX XML 简化为 LLM 友好的结构。

    返回:
      {
        "entity_sets": [
            {"name": ..., "entity_type": ..., "keys": [...], "properties": [...]},
            ...
        ],
        "raw_size_bytes": int
      }
    """
    root = ET.fromstring(xml_text)

    # 收集 EntityType: 局部名 -> {properties, keys}
    entity_types: dict[str, dict[str, Any]] = {}
    for et in root.iter():
        if _local(et.tag) != "EntityType":
            continue
        name = et.attrib.get("Name")
        if not name:
            continue
        props: list[dict[str, Any]] = []
        keys: list[str] = []
        for child in et:
            local = _local(child.tag)
            if local == "Property":
                props.append({
                    "name": child.attrib.get("Name"),
                    "type": child.attrib.get("Type"),
                    "nullable": child.attrib.get("Nullable", "true"),
                    "label": (
                        child.attrib.get("{http://www.sap.com/Protocols/SAPData}label")
                        or child.attrib.get("sap:label")
                    ),
                })
            elif local == "Key":
                for ref in child:
                    if _local(ref.tag) == "PropertyRef":
                        keys.append(ref.attrib.get("Name", ""))
        entity_types[name] = {"properties": props, "keys": keys}

    # EntitySet
    entity_sets: list[dict[str, Any]] = []
    for es in root.iter():
        if _local(es.tag) != "EntitySet":
            continue
        name = es.attrib.get("Name")
        et_qualified = es.attrib.get("EntityType", "")
        et_local = et_qualified.rsplit(".", 1)[-1]
        info = entity_types.get(et_local, {})
        entity_sets.append({
            "name": name,
            "entity_type": et_qualified,
            "keys": info.get("keys", []),
            "properties": info.get("properties", []),
        })

    return {"entity_sets": entity_sets, "raw_size_bytes": len(xml_text)}


def _strip_metadata(obj: Any) -> Any:
    """OData V2 行里有大量 __metadata / __deferred 噪声字段,剥除。"""
    if isinstance(obj, list):
        return [_strip_metadata(x) for x in obj]
    if isinstance(obj, dict):
        return {
            k: _strip_metadata(v)
            for k, v in obj.items()
            if k != "__metadata"
            and not (isinstance(v, dict) and "__deferred" in v)
        }
    return obj


def _parse_odata_xml_rows(xml_text: str) -> dict[str, Any] | None:
    """解析 OData V2 Atom/XML 查询结果为表格行。

    典型结构:
      <feed>
        <m:count>...</m:count>
        <entry>
          <content>
            <m:properties>
              <d:Field>value</d:Field>
            </m:properties>
          </content>
        </entry>
      </feed>
    """
    text = (xml_text or "").strip()
    if not text.startswith("<"):
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    rows: list[dict[str, Any]] = []
    row_count_total: str | None = None
    next_url: str | None = None

    for node in root.iter():
        local = _local(node.tag)
        if local == "count" and row_count_total is None:
            row_count_total = (node.text or "").strip() or None
        if local == "link" and node.attrib.get("rel") == "next" and not next_url:
            next_url = node.attrib.get("href") or None
        if local != "entry":
            continue
        props = None
        for child in node.iter():
            if _local(child.tag) == "properties":
                props = child
                break
        if props is None:
            continue
        row: dict[str, Any] = {}
        for prop in list(props):
            key = _local(prop.tag)
            is_null = any(
                attr_name.endswith("}null") and str(attr_val).lower() == "true"
                for attr_name, attr_val in prop.attrib.items()
            )
            row[key] = None if is_null else (prop.text or "")
        if row:
            rows.append(row)

    if not rows and _local(root.tag) == "entry":
        props = None
        for child in root.iter():
            if _local(child.tag) == "properties":
                props = child
                break
        if props is not None:
            row = {}
            for prop in list(props):
                key = _local(prop.tag)
                is_null = any(
                    attr_name.endswith("}null") and str(attr_val).lower() == "true"
                    for attr_name, attr_val in prop.attrib.items()
                )
                row[key] = None if is_null else (prop.text or "")
            if row:
                rows.append(row)

    if not rows:
        return None

    return {
        "rows": rows,                 # 不截断:分页/导出需要完整行
        "row_count_returned": len(rows),
        "row_count_total": row_count_total,
        "next_url": next_url,
    }
