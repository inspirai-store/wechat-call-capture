param(
    [string]$VcpkgRoot = $(if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { 'C:\vcpkg' })
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$upstream = Join-Path $projectRoot 'usbip-experiment\usbipdcpp'
$buildDir = Join-Path $projectRoot 'usbip-experiment\build'
$patch = Join-Path $projectRoot 'usbip-source\usbipdcpp.patch'
$revision = '51ec41981024250d068fda25aed88be9140af722'
$toolchain = Join-Path $VcpkgRoot 'scripts\buildsystems\vcpkg.cmake'
if (-not (Test-Path -LiteralPath $toolchain)) { throw "vcpkg toolchain not found: $toolchain" }

function Invoke-Checked([string]$program, [string[]]$arguments) {
    & $program @arguments
    if ($LASTEXITCODE -ne 0) { throw "$program failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $upstream)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $upstream) -Force | Out-Null
    Invoke-Checked git @('clone', 'https://github.com/yunsmall/usbipdcpp.git', $upstream)
    Invoke-Checked git @('-C', $upstream, 'checkout', '--detach', $revision)
}
$current = (& git -C $upstream rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $current -ne $revision) { throw "Unexpected usbipdcpp checkout: $current" }
$savedPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 otherwise raises on git's expected check failure.
    $ErrorActionPreference = 'Continue'
    & git -C $upstream apply --check $patch 2>$null
    $canApply = $LASTEXITCODE -eq 0
    & git -C $upstream apply --reverse --check $patch 2>$null
    $alreadyApplied = $LASTEXITCODE -eq 0
} finally {
    $ErrorActionPreference = $savedPreference
}
if ($canApply) {
    Invoke-Checked git @('-C', $upstream, 'apply', $patch)
} elseif (-not $alreadyApplied) {
    throw 'Cannot apply the usbipdcpp patch; use a clean checkout.'
}
$example = Join-Path $upstream 'examples\wechat_uvc'
New-Item -ItemType Directory -Path $example -Force | Out-Null
foreach ($file in @('CMakeLists.txt', 'LiveSource.cpp', 'LiveSource.h', 'wechat_uvc_main.cpp')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "usbip-source\$file") -Destination $example -Force
}

Invoke-Checked cargo @('build', '--release', '--locked', '--manifest-path', (Join-Path $projectRoot 'native\Cargo.toml'))
$previousCl = $env:CL
try {
    $env:CL = "$previousCl /MP4 /D_WIN32_WINNT=0x0A00"
    Invoke-Checked cmake @('-S', $upstream, '-B', $buildDir, '-G', 'Visual Studio 17 2022', '-A', 'x64',
        "-DCMAKE_TOOLCHAIN_FILE=$toolchain", '-DVCPKG_TARGET_TRIPLET=x64-windows',
        '-DUSBIPDCPP_BUILD_LIBUSB_COMPONENTS=OFF', '-DUSBIPDCPP_BUILD_TESTS=OFF',
        '-DUSBIPDCPP_BUILD_EXAMPLES=ON', '-DUSBIPDCPP_BUILD_VIRTUAL_DEVICE=ON')
    Invoke-Checked cmake @('--build', $buildDir, '--config', 'Release', '--target', 'wechat_uvc_pair', '-j', '4')
} finally {
    $env:CL = $previousCl
}
Write-Host 'Native components built successfully.'
