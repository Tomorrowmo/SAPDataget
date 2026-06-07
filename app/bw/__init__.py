"""BW 数据层 —— Live / Mock 双实现，通过 make_bw_client 选择。"""
from app.bw.interface import BWClient, ODataResponse, ODataError
from app.bw.factory import make_bw_client

__all__ = ["BWClient", "ODataResponse", "ODataError", "make_bw_client"]
