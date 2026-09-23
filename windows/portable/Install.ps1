param([switch]$NoPrompt)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$expectedFiles = @('GUI\BondSealGUI.exe','CLI\BondSealCLI.exe','windows\BondSealContextMenu.dll')
foreach ($name in $expectedFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $name) -PathType Leaf)) {
        throw "安装包不完整：缺少 $name"
    }
}
$installRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\BondSealPages'))
$versionName = 'app-' + [guid]::NewGuid().ToString('N')
$versionRoot = Join-Path $installRoot $versionName
$clsid = '{62C7EB66-A11D-4F91-934C-3DAA9A3EF821}'
$comKey = "HKCU:\Software\Classes\CLSID\$clsid"
$legacyVerbKey = 'HKCU:\Software\Classes\*\shell\BondSealCollect'

New-Item -ItemType Directory -Force -Path $installRoot,$versionRoot | Out-Null
foreach ($name in @('GUI','CLI','windows')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $versionRoot $name) -Recurse -Force
}

$dllPath = Join-Path $versionRoot 'windows\BondSealContextMenu.dll'
$guiPath = Join-Path $versionRoot 'GUI\BondSealGUI.exe'
$cliPath = Join-Path $versionRoot 'CLI\BondSealCLI.exe'
foreach ($path in @($dllPath,$guiPath,$cliPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "安装文件缺失：$path" }
}

$comRegistration = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Software\Classes\CLSID\$clsid")
try { $comRegistration.SetValue('', 'BondSeal Explorer Command', [Microsoft.Win32.RegistryValueKind]::String) }
finally { $comRegistration.Close() }
$serverRegistration = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Software\Classes\CLSID\$clsid\InprocServer32")
try {
    $serverRegistration.SetValue('', $dllPath, [Microsoft.Win32.RegistryValueKind]::String)
    $serverRegistration.SetValue('ThreadingModel', 'Apartment', [Microsoft.Win32.RegistryValueKind]::String)
}
finally { $serverRegistration.Close() }
foreach ($extension in @('.doc','.docx')) {
    $relative = "Software\Classes\SystemFileAssociations\$extension\shell\BondSealCollect"
    $verbRegistration = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($relative)
    try {
        $verbRegistration.SetValue('', '生成签署页合集', [Microsoft.Win32.RegistryValueKind]::String)
        $verbRegistration.SetValue('ExplorerCommandHandler', $clsid, [Microsoft.Win32.RegistryValueKind]::String)
        $verbRegistration.SetValue('MultiSelectModel', 'Player', [Microsoft.Win32.RegistryValueKind]::String)
    }
    finally { $verbRegistration.Close() }
}
if (Test-Path -LiteralPath $legacyVerbKey) {
    $legacy = Get-Item -LiteralPath $legacyVerbKey
    if ($legacy.GetValue('ExplorerCommandHandler') -eq $clsid) {
        Remove-Item -LiteralPath $legacyVerbKey -Recurse -Force
    }
}

Copy-Item -LiteralPath (Join-Path $sourceRoot 'Uninstall.ps1') -Destination (Join-Path $installRoot 'Uninstall.ps1') -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'Uninstall.cmd') -Destination (Join-Path $installRoot 'Uninstall.cmd') -Force
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $installRoot 'current.txt'),$versionName,$utf8)
$launcher = '@echo off' + [Environment]::NewLine + '"%~dp0' + $versionName + '\CLI\BondSealCLI.exe" %*' + [Environment]::NewLine
[IO.File]::WriteAllText((Join-Path $installRoot 'bondseal.cmd'),$launcher,$utf8)
$installedPrefix = $installRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($directory in Get-ChildItem -LiteralPath $installRoot -Directory) {
    if ($directory.Name -eq $versionName -or $directory.Name -notmatch '^app-[0-9a-f]{32}$') { continue }
    $oldPath = [IO.Path]::GetFullPath($directory.FullName)
    if (-not $oldPath.StartsWith($installedPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw "旧版本目录超出安装位置：$oldPath"
    }
    try { Remove-Item -LiteralPath $oldPath -Recurse -Force -ErrorAction Stop }
    catch { Write-Warning "旧版本仍被系统占用；重启后再次安装可清理：$oldPath" }
}
$refreshType = @'
using System;
using System.Runtime.InteropServices;
public static class BondSealShellRefresh {
    [DllImport("shell32.dll", ExactSpelling=true)]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@
Add-Type -TypeDefinition $refreshType
[BondSealShellRefresh]::SHChangeNotify(0x08000000,0,[IntPtr]::Zero,[IntPtr]::Zero)
Write-Host '安装完成。选中同一文件夹中的 Word，右键 → 显示更多选项 → 生成签署页合集。'
Write-Host "Agent/命令行入口：$(Join-Path $installRoot 'bondseal.cmd')"
Write-Host "卸载入口：$(Join-Path $installRoot 'Uninstall.cmd')"
