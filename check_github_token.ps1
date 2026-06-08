Set-Location $PSScriptRoot

if (-not $env:GITHUB_TOKEN) {
    Write-Error "GITHUB_TOKEN is not set in this terminal. Run: `$env:GITHUB_TOKEN=\"your_new_token\""
    exit 2
}

$headers = @{
    Authorization = "Bearer $env:GITHUB_TOKEN"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

function Show-HttpError($label, $err) {
    Write-Host "[$label] FAILED" -ForegroundColor Red
    if ($err.Exception.Response) {
        Write-Host "HTTP:" ([int]$err.Exception.Response.StatusCode.value__)
        try {
            $reader = [System.IO.StreamReader]::new($err.Exception.Response.GetResponseStream())
            $body = $reader.ReadToEnd()
            if ($body) { Write-Host $body }
        } catch {}
    } else {
        Write-Host $err.Exception.Message
    }
}

try {
    $me = Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers -Method Get
    Write-Host "TOKEN_USER=$($me.login)" -ForegroundColor Green
} catch {
    Show-HttpError "GET /user" $_
    exit 3
}

try {
    $repo = Invoke-RestMethod -Uri "https://api.github.com/repos/Tomorrowmo/sapagent" -Headers $headers -Method Get
    Write-Host "REPO_FOUND=$($repo.full_name) private=$($repo.private)" -ForegroundColor Green
    if ($repo.permissions) {
        Write-Host "PERMISSIONS admin=$($repo.permissions.admin) maintain=$($repo.permissions.maintain) push=$($repo.permissions.push) triage=$($repo.permissions.triage) pull=$($repo.permissions.pull)"
    } else {
        Write-Host "PERMISSIONS field not returned; for fine-grained token verify Contents: Read and write."
    }
} catch {
    Show-HttpError "GET /repos/Tomorrowmo/sapagent" $_
    exit 4
}

try {
    $refs = Invoke-RestMethod -Uri "https://api.github.com/repos/Tomorrowmo/sapagent/git/matching-refs/heads" -Headers $headers -Method Get
    Write-Host "REMOTE_HEAD_REFS=$($refs.Count)"
} catch {
    Show-HttpError "GET refs" $_
}

Write-Host "If push=false or repo access fails, create a new token with repo scope (classic) or Contents: Read and write for Tomorrowmo/sapagent (fine-grained)." -ForegroundColor Yellow
