$ErrorActionPreference = 'Stop'
$tools = Join-Path $PSScriptRoot 'tools'
$path = Join-Path $tools 'cloudflared.exe'
$temporary = Join-Path $tools 'cloudflared-download.tmp'
$expected = '2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
try {
    Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-windows-amd64.exe' -OutFile $temporary
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $expected) {
        throw 'cloudflared SHA256 mismatch; download was not installed.'
    }
    Move-Item -LiteralPath $temporary -Destination $path -Force
    & $path --version
    if ($LASTEXITCODE -ne 0) { throw 'cloudflared failed to run.' }
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
