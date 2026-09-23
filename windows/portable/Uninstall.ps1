param([switch]$NoPrompt)
$ErrorActionPreference = 'Stop'
$installRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\BondSealPages'))
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\BondSealPages'))
if (-not $installRoot.Equals($expectedRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid installation root.' }
$clsid = '{62C7EB66-A11D-4F91-934C-3DAA9A3EF821}'
$comKey = "HKCU:\Software\Classes\CLSID\$clsid"
$verbKey = 'HKCU:\Software\Classes\*\shell\BondSealCollect'

$serverKey = Join-Path $comKey 'InprocServer32'
$serverPath = if (Test-Path -LiteralPath $serverKey) { (Get-Item -LiteralPath $serverKey).GetValue('') } else { $null }
if ($serverPath) {
    $fullServer = [IO.Path]::GetFullPath($serverPath)
    $prefix = $installRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $fullServer.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw "右键扩展指向其他安装位置，已停止卸载：$fullServer"
    }
}
if (Test-Path -LiteralPath $verbKey) { Remove-Item -LiteralPath $verbKey -Recurse -Force }
if (Test-Path -LiteralPath $comKey) { Remove-Item -LiteralPath $comKey -Recurse -Force }
if (Test-Path -LiteralPath $installRoot) {
    $marker = Join-Path $installRoot 'current.txt'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        throw "缺少安装标记，已保留文件：$installRoot"
    }
    try {
        Remove-Item -LiteralPath $installRoot -Recurse -Force
        Write-Host '卸载完成。'
    }
    catch {
        Write-Warning '菜单已移除，但资源管理器仍可能占用旧 DLL。请重启 Windows 后删除以下目录：'
        Write-Warning $installRoot
    }
}
else { Write-Host '右键菜单已移除。' }
