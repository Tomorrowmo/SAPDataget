"""BWClient 接口契约 —— Live / Mock 两个实现都遵守。

设计要点 (§8.2):
  * Protocol 而非 ABC: 鸭子类型,便于 Mock / 测试
  * ODataResponse 是标准化的响应结构,屏蔽 HTTP 细节
  * execute_query 参数对齐 OData V2 系统查询选项
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ODataError(Exception):
    """BWClient 层的统一异常。"""

    def __init__(self, message: str, status_code: int = 0, url: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


@dataclass
class ODataResponse:
    """所有 BW 请求的标准化返回。

    错误用 error 字段表示，不抛异常 —— 让上层 LLM 能看到错误内容并自我纠错。
    """
    status_code: int            # HTTP 状态码（Mock 模式下用模拟值）
    url: str                    # 请求 URL（Mock 模式下用伪 URL,保留可读性）
    json: Any | None = None     # 解析后的结构化数据
    text: str = ""              # 非 JSON 响应体或错误文本
    error: str | None = None    # 业务错误描述（如 "字段不存在"）

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and self.error is None


class BWClient(Protocol):
    """BW OData V2 客户端契约。

    Live 实现走 HTTP 到 SAP NetWeaver Gateway。
    Mock 实现走本地 mock_data 目录 + pandas 内存查询。
    """

    def list_services(self, search: str | None = None, top: int = 50) -> ODataResponse:
        """通过 Gateway 目录服务列出可用的 OData 服务。

        返回 data.services: [{TechnicalServiceName, Title, Description, Version, ServiceUrl}, ...]
        """
        ...

    def get_metadata(self, service: str) -> ODataResponse:
        """获取并简化某服务的 $metadata。

        返回 data.entity_sets: [{name, entity_type, keys, properties:[{name,type,label}]}]
        """
        ...

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
        """执行 OData V2 查询。

        参数对齐标准系统查询选项；不支持的参数实现方应返回 400 + error 文案,
        而非静默忽略。
        """
        ...

    def describe(self) -> str:
        """一句话描述自身（mock / live + 端点）—— 用于启动日志与 UI 角标。"""
        ...
