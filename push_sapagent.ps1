Set-Location $PSScriptRoot

if (-not $env:GITHUB_TOKEN) {
    Write-Error "GITHUB_TOKEN is not set in this terminal. Run: `$env:GITHUB_TOKEN=\"your_new_token\""
    exit 2
}

$code = @'
import os
from urllib.parse import quote
from dulwich import porcelain
from dulwich.repo import Repo

token = os.environ.get("GITHUB_TOKEN", "")
if not token:
    raise SystemExit("GITHUB_TOKEN missing")

repo = Repo(".")
repo.get_config().set((b"remote", b"origin"), b"url", b"https://github.com/Tomorrowmo/sapagent.git")
repo.get_config().write_to_path()

url = "https://x-access-token:%s@github.com/Tomorrowmo/sapagent.git" % quote(token, safe="")
porcelain.push(".", url, b"refs/heads/main:refs/heads/main")
print("PUSH_OK https://github.com/Tomorrowmo/sapagent.git")
'@

Set-Content -Path ".tmp_push_sapagent.py" -Value $code -Encoding UTF8
try {
    .\.venv\Scripts\python.exe .tmp_push_sapagent.py
}
finally {
    Remove-Item ".tmp_push_sapagent.py" -Force -ErrorAction SilentlyContinue
}
