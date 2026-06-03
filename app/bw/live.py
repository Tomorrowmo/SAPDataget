"""LiveBWClient —— 真实 SAP BW 7.5 NetWeaver Gateway 客户端。

封装内容（与 §8.3 一致）:
  * HTTP Basic Auth + sap-client + sap-language
  * 服务目录 /sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection
  * $metadata 拉取 + EDMX 简化解析（去掉噪声字段,只保留 entity_sets/properties）
  * 通用 OData V2 查询执行
  * 错误归一化为 ODataResponse
"""
from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import requests
import urllib3
from requests.auth import HTTPBasicAuth

from app.bw.interface import BWClient, ODataResponse
from app.config import BWSettings

CATALOG_PATH = "/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection"
ODATA_SERVICE_ROOT = "/sap/opu/odata/sap"


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
        return self.session.get(
            url,
            params=merged,
            headers=headers,
            timeout=self.settings.timeout,
        )

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
        path = f"{ODATA_SERVICE_ROOT}/{service.strip('/')}/$metadata"
        try:
            r = self._get(path, accept_json=False)
        except requests.RequestException as e:
            return ODataResponse(0, path, error=f"请求异常: {e}")
        if not r.ok:
            return ODataResponse(
                r.status_code, r.url,
                text=r.text[:2000],
                error=f"HTTP {r.status_code}",
            )
        try:
            simplified = _parse_metadata(r.text)
        except Exception as e:
            return ODataResponse(
                r.status_code, r.url,
                text=r.text[:2000],
                error=f"$metadata 解析失败: {e}",
            )
        return ODataResponse(r.status_code, r.url, json=simplified)

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

        body: Any = None
        try:
            body = r.json()
        except ValueError:
            pass

        if isinstance(body, dict) and "d" in body:
            d = body["d"]
            if isinstance(d, dict) and "results" in d:
                rows = d.get("results", [])
                payload = {
                    "rows": _strip_metadata(rows[:200]),
                    "row_count_returned": len(rows),
                    "row_count_total": d.get("__count"),
                }
                return ODataResponse(
                    r.status_code, r.url,
                    json=payload,
                    error=None if r.ok else f"HTTP {r.status_code}",
                )
            return ODataResponse(
                r.status_code, r.url,
                json={"value": _strip_metadata(d)},
                error=None if r.ok else f"HTTP {r.status_code}",
            )

        return ODataResponse(
            r.status_code, r.url,
            text=r.text[:2000],
            error=None if r.ok else f"HTTP {r.status_code}",
        )


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
