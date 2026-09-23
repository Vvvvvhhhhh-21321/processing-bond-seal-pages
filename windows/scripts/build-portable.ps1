$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$artifactRoot = Join-Path $repoRoot 'artifacts'
$artifactPrefix = [IO.Path]::GetFullPath($artifactRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

function Clear-BuildPath([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($artifactPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Build path is outside artifacts: $full" }
    if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
}

$msbuild = Get-Command MSBuild.exe -ErrorAction SilentlyContinue
if (-not $msbuild) { $msbuild = Get-Command msbuild -ErrorAction SilentlyContinue }
if (-not $msbuild) { throw 'Visual Studio 2022 C++ Build Tools are required.' }

Push-Location $repoRoot
try {
    uv sync --locked --group build
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }
    & $msbuild.Source windows\explorer_command\BondSealContextMenu.vcxproj /m /p:Configuration=Release /p:Platform=x64
    if ($LASTEXITCODE -ne 0) { throw 'Native context-menu DLL build failed.' }

    $dist = Join-Path $artifactRoot 'dist'
    $work = Join-Path $artifactRoot 'pyinstaller-work'
    New-Item -ItemType Directory -Force -Path $dist,$work | Out-Null
    foreach ($name in @('gui','cli','worker')) {
        $distPath = Join-Path $dist $name
        $workPath = Join-Path $work $name
        Clear-BuildPath $distPath
        Clear-BuildPath $workPath
        New-Item -ItemType Directory -Force -Path $distPath,$workPath | Out-Null
        uv run --locked --group build pyinstaller --clean --noconfirm --distpath $distPath --workpath $workPath ("desktop\pyinstaller\" + $name + '.spec')
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $name." }
    }

    $gui = Join-Path $dist 'gui\BondSealGUI'
    $cli = Join-Path $dist 'cli\BondSealCLI'
    $worker = Join-Path $dist 'worker\BondSealWordWorker.exe'
    $dll = Join-Path $artifactRoot 'native\BondSealContextMenu.dll'
    foreach ($path in @($gui,$cli,$worker,$dll)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing build output: $path" }
    }

    $stage = Join-Path $artifactRoot 'portable-stage'
    Clear-BuildPath $stage
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'GUI'),(Join-Path $stage 'CLI'),(Join-Path $stage 'windows') | Out-Null
    Copy-Item -Path (Join-Path $gui '*') -Destination (Join-Path $stage 'GUI') -Recurse -Force
    Copy-Item -Path (Join-Path $cli '*') -Destination (Join-Path $stage 'CLI') -Recurse -Force
    Copy-Item -LiteralPath $worker -Destination (Join-Path $stage 'GUI\BondSealWordWorker.exe')
    Copy-Item -LiteralPath $worker -Destination (Join-Path $stage 'CLI\BondSealWordWorker.exe')
    Copy-Item -LiteralPath $dll -Destination (Join-Path $stage 'windows\BondSealContextMenu.dll')
    foreach ($name in @('Install.ps1','Install.cmd','Uninstall.ps1','Uninstall.cmd','bondseal.cmd')) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "windows\portable\$name") -Destination (Join-Path $stage $name)
    }

    $zip = Join-Path $artifactRoot 'BondSealPages-Windows-x64.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "Portable package: $zip"
}
finally { Pop-Location }
