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
$verbKey = 'HKCU:\Software\Classes\*\shell\BondSealCollect'

New-Item -ItemType Directory -Force -Path $installRoot,$versionRoot | Out-Null
foreach ($name in @('GUI','CLI','windows')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $versionRoot $name) -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $sourceRoot 'Uninstall.ps1') -Destination (Join-Path $versionRoot 'Uninstall.ps1')
Copy-Item -LiteralPath (Join-Path $sourceRoot 'Uninstall.cmd') -Destination (Join-Path $installRoot 'Uninstall.cmd')

$dllPath = Join-Path $versionRoot 'windows\BondSealContextMenu.dll'
$guiPath = Join-Path $versionRoot 'GUI\BondSealGUI.exe'
$cliPath = Join-Path $versionRoot 'CLI\BondSealCLI.exe'
foreach ($path in @($dllPath,$guiPath,$cliPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "安装文件缺失：$path" }
}

New-Item -Path $comKey -Force -Value 'BondSeal Explorer Command' | Out-Null
$serverKey = New-Item -Path (Join-Path $comKey 'InprocServer32') -Force -Value $dllPath
New-ItemProperty -Path $serverKey.PSPath -Name 'ThreadingModel' -Value 'Apartment' -PropertyType String -Force | Out-Null
New-Item -Path $verbKey -Force -Value '生成签署页合集' | Out-Null
New-ItemProperty -Path $verbKey -Name 'ExplorerCommandHandler' -Value $clsid -PropertyType String -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $sourceRoot 'Uninstall.ps1') -Destination (Join-Path $installRoot 'Uninstall.ps1') -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'Uninstall.cmd') -Destination (Join-Path $installRoot 'Uninstall.cmd') -Force
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $installRoot 'current.txt'),$versionName,$utf8)
$launcher = '@echo off' + [Environment]::NewLine + '"%~dp0' + $versionName + '\CLI\BondSealCLI.exe" %*' + [Environment]::NewLine
[IO.File]::WriteAllText((Join-Path $installRoot 'bondseal.cmd'),$launcher,$utf8)
Write-Host '安装完成。选中同一文件夹中的 Word，右键 → 显示更多选项 → 生成签署页合集。'
Write-Host "Agent/命令行入口：$(Join-Path $installRoot 'bondseal.cmd')"
Write-Host "卸载入口：$(Join-Path $installRoot 'Uninstall.cmd')"
