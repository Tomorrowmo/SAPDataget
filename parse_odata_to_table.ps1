param(
    [string]$Url = "http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet",
    [string]$OutCsv = ".\\odata_table.csv",
    [int]$Top = 0
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$username = Read-Host "SAP Username"
$password = Read-Host "SAP Password" -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential($username, $password)

$finalUrl = $Url
if ($Top -gt 0) {
    if ($finalUrl.Contains("?")) {
        $finalUrl = "$finalUrl&`$top=$Top"
    } else {
        $finalUrl = "$finalUrl?`$top=$Top"
    }
}

$response = Invoke-WebRequest `
    -Uri $finalUrl `
    -Credential $credential `
    -Method Get `
    -UseBasicParsing `
    -Headers @{ "Accept" = "application/atom+xml,application/xml,text/xml" }

[xml]$xml = $response.Content

$ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
$ns.AddNamespace("atom", "http://www.w3.org/2005/Atom")
$ns.AddNamespace("m", "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata")
$ns.AddNamespace("d", "http://schemas.microsoft.com/ado/2007/08/dataservices")

$entries = $xml.SelectNodes("//atom:entry", $ns)
if (-not $entries -or $entries.Count -eq 0) {
    throw "No <entry> found in XML. Please verify URL or credentials."
}

# Collect all columns in appearance order.
$columnOrder = New-Object System.Collections.Generic.List[string]
foreach ($entry in $entries) {
    $props = $entry.SelectSingleNode("atom:content/m:properties", $ns)
    if ($props) {
        foreach ($node in $props.ChildNodes) {
            $name = $node.LocalName
            if (-not $columnOrder.Contains($name)) {
                [void]$columnOrder.Add($name)
            }
        }
    }
}

$rows = New-Object System.Collections.Generic.List[object]
foreach ($entry in $entries) {
    $props = $entry.SelectSingleNode("atom:content/m:properties", $ns)
    if (-not $props) { continue }

    $map = @{}
    foreach ($node in $props.ChildNodes) {
        $isNull = $false
        foreach ($attr in $node.Attributes) {
            if ($attr.LocalName -eq "null" -and $attr.Value -eq "true") {
                $isNull = $true
                break
            }
        }

        if ($isNull) {
            $map[$node.LocalName] = $null
        } else {
            $map[$node.LocalName] = $node.InnerText
        }
    }

    $obj = [ordered]@{}
    foreach ($col in $columnOrder) {
        if ($map.ContainsKey($col)) {
            $obj[$col] = $map[$col]
        } else {
            $obj[$col] = $null
        }
    }

    $rows.Add([pscustomobject]$obj) | Out-Null
}

if ($rows.Count -eq 0) {
    throw "No data rows parsed from OData XML."
}

$rows | Export-Csv -Path $OutCsv -NoTypeInformation -Encoding UTF8

Write-Output "Parsed rows: $($rows.Count)"
Write-Output "Columns: $($columnOrder.Count)"
Write-Output "CSV: $((Resolve-Path $OutCsv).Path)"
Write-Output ""
Write-Output "Preview (first 10 rows):"
$rows | Select-Object -First 10 | Format-Table -AutoSize
