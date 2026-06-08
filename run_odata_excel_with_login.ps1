param(
    [string]$Url = "http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet",
    [string]$OutXlsx = ".\\data\\outputs\\LtResultSet.xlsx",
    [string]$OutCsv = ".\\data\\outputs\\LtResultSet.csv"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Popup credential dialog (easier than terminal prompt).
$cred = Get-Credential -Message "Please enter SAP username and password"
if (-not $cred) {
    throw "Credential input was cancelled."
}

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($cred.Password)
try {
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)

    $env:SAP_USERNAME = $cred.UserName
    $env:SAP_PASSWORD = $plainPassword

    if (Test-Path ".\\.venv\\Scripts\\python.exe") {
        $python = ".\\.venv\\Scripts\\python.exe"
    } else {
        $python = "python"
    }

    & $python .\odata_xml_to_excel.py --url $Url --out-xlsx $OutXlsx --out-csv $OutCsv
    if ($LASTEXITCODE -ne 0) {
        throw "odata_xml_to_excel.py failed with exit code $LASTEXITCODE"
    }
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    Remove-Item Env:SAP_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:SAP_PASSWORD -ErrorAction SilentlyContinue
}

Write-Output "Done."
Write-Output "Excel: $((Resolve-Path $OutXlsx).Path)"
Write-Output "CSV:   $((Resolve-Path $OutCsv).Path)"
