# SAP OData 探针报告
- 时间: 2026-06-09 07:53:03
- 主机: http://sapbd1app01.cn.schneider-electric.com:8000  client=300  user=sesa550670  ssl_verify=True
- 报告清单服务: ZBW_QUERY_LIST_SRV/LtResultSet  | 配置归属字段 OWNER_FIELD=UName

> ⚠ 含少量样本值,发出前请打码敏感业务数据。

## 1) 报告清单原始响应(不带 $top,看默认单页返回多少 + 总数 + 是否分页)
- HTTP 200, Content-Type: application/atom+xml; charset=utf-8
- <m:count> 总数 = **None**  | 本次响应实际 entry 条数 = **8506**  | 分页 next 链接 = 无
- 字段名: ['Uname', 'Compid', 'Txtlg']
原始响应片段:
```
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" xml:base="http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/"><id>http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet</id><title type="text">LtResultSet</title><updated>2026-06-08T23:53:03Z</updated><author><name/></author><link href="LtResultSet" rel="self" title="LtResultSet"/><entry><id>http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet(Uname='SESA10052',Compid='ZHKI_M01_Q001')</id><title type="text">LtResultSet(Uname='SESA10052',Compid='ZHKI_M01_Q001')</title><updated>2026-06-08T23:53:03Z</updated><category term="ZBW_QUERY_LIST_SRV.LtResult" scheme="http://schemas.microsoft.com/ado/2007/08/dataservices/scheme"/><link href="LtResultSet(Uname='SESA10052',Compid='ZHKI_M01_Q001')" rel="self" title="LtResult"/><content type="application/xml"><m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><d:Uname>SESA10052</d:Uname><d:Compid>ZHKI_M01_Q001</d:Compid><d:Txtlg>HK Report: SPA USAGE</d:Txtlg></m:properties></content></entry><entry><id>http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet(Uname='SESA10052',Compid='ZHKI_M01_Q002')</id><title type="text">LtResultSet(Uname='SESA10052',Compid='ZHKI_M01_Q002')</title><updated>2026-06-08T23:53:03Z</updated><category term="ZBW_QUERY_LIST_SRV.LtResult" scheme="http://schemas.microsoft.com/ado/2007/08/dataservices/scheme"/><link href="LtResultSet(Uname='SESA10052',Compid='ZHKI_M01_Q002')" rel="self" title="LtResult"/><content type="application/xml"><m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/
…(截断,原长 7042589)
```

## 2) 报告清单 $metadata(字段 Edm 类型 + 主键 → 定位归属用户字段)
- EntitySet LtResultSet 主键: ['Uname', 'Compid']
  - Uname  (Edm.String)  label=None
  - Compid  (Edm.String)  label=None
  - Txtlg  (Edm.String)  label=None

## 3) 服务端按用户过滤是否生效($filter=<归属字段> eq '你的用户大写')
- 候选归属字段: ['Uname']  | 用户值(大写): 'SESA550670'
  - `Uname`: ✅ 成功  你的条数(__count)=None  本页样本=5
```
[
  {
    "Uname": "SESA10052",
    "Compid": "ZHKI_M01_Q001",
    "Txtlg": "HK Report: SPA USAGE"
  },
  {
    "Uname": "SESA10052",
    "Compid": "ZHKI_M01_Q002",
    "Txtlg": "HK Report: Discount Reprt by orders"
  }
]
```

## 4) 分页是否生效(execute_query top=1000,看能否跟 __next 拿多页)
- 拿回 row_count_returned=1000  | row_count_total=None
- 若 returned 远大于单页(第1节的 entry 条数),说明分页生效;若 ≈ 单页,说明该服务未给 __next。

## 5) SAP 错误体格式(查一个不存在的字段,抓真实报错结构)
- 客户端归一化后的 error = 'Property ZZ_NoSuchField not found in type LtResult'
- 原始错误体片段:
```
{"error":{"code":"005056A509B11EE1B9A8FEA8DE87F78E","message":{"lang":"zh","value":"Property ZZ_NoSuchField not found in type LtResult"},"innererror":{"transactionid":"137FEBC5E74C0000E006A274FA733708","timestamp":"20260608235304.4570090","Error_Resolution":{"SAP_Transaction":"For backend administrators: run transaction /IWFND/ERROR_LOG on SAP Gateway hub system and search for entries with the timestamp above for more details","SAP_Note":"See SAP Note 1797736 for error analysis (https://service.sap.com/sap/support/notes/1797736)"}}}}
```

## 6) 目录服务(前 10 个 OData 服务)
- 失败: HTTP 404

## 7) 业务服务原始编码 ZBW_SALES_SRV/SalesByOfficeView(看 Decimal/DateTime 真实编码)
- HTTP 403
- 原始 JSON(注意金额是否为字符串、日期是否 /Date(ms)/):
```
{"error":{"code":"/IWFND/MED/170","message":{"lang":"zh","value":"未找到命名空间为 ''，名称为 'ZBW_SALES_SRV'，版本为 '0001' 的服务"},"innererror":{"application":{"component_id":"","service_namespace":"/SAP/","service_id":"ZBW_SALES_SRV","service_version":"0001"},"transactionid":"137FEBC5E74C0000E006A274FA73370B","timestamp":"20260608235304.6383550","Error_Resolution":{"SAP_Transaction":"For backend administrators: run transaction /IWFND/ERROR_LOG on SAP Gateway hub system and search for entries with the timestamp above for more details","SAP_Note":"See SAP Note 1797736 for error analysis (https://service.sap.com/sap/support/notes/1797736)"},"errordetails":[]}}}
```