param([string]$Version="0.1.0.0",[string]$Publisher="CN=BondSealPages-Development",[switch]$SignForInternalTest)
$ErrorActionPreference="Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw "Version must have four numeric components." }
if ($Publisher -match '["\r\n]') { throw "Publisher contains an invalid character." }
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$artifactRoot=Join-Path $repoRoot "artifacts"
$prefix=[IO.Path]::GetFullPath($artifactRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)+[IO.Path]::DirectorySeparatorChar
function Clear-BuildPath([string]$p) {
  $full=[IO.Path]::GetFullPath($p)
  if (-not $full.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw "Outside artifacts" }
  if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
}
function Find-SdkTool([string]$n) {
  $g=Get-Command $n -ErrorAction SilentlyContinue
  if ($g) { return $g.Source }
  $sdk=Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Windows Kits\10\bin"
  return Get-ChildItem $sdk -Filter $n -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.DirectoryName -match '\\x64$' } | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
$msbuild=Get-Command MSBuild.exe -ErrorAction SilentlyContinue
if (-not $msbuild) { $msbuild=Get-Command msbuild -ErrorAction SilentlyContinue }
if (-not $msbuild) { throw "Visual Studio 2022 C++ Build Tools are required." }
$makeAppx=Find-SdkTool "MakeAppx.exe"
$signTool=Find-SdkTool "SignTool.exe"
if (-not $makeAppx) { throw "Windows SDK MakeAppx.exe is required." }
Push-Location $repoRoot
try {
  uv sync --locked --group build
  if ($LASTEXITCODE -ne 0) { throw "uv sync failed." }
  & $msbuild.Source windows\explorer_command\BondSealContextMenu.vcxproj /m /p:Configuration=Release /p:Platform=x64
  if ($LASTEXITCODE -ne 0) { throw "Native COM DLL build failed." }
  $dist=Join-Path $artifactRoot "dist"; $work=Join-Path $artifactRoot "pyinstaller-work"
  New-Item -ItemType Directory -Force -Path $dist,$work | Out-Null
  foreach ($name in @("gui","cli","worker")) {
    $distPath=Join-Path $dist $name; $workPath=Join-Path $work $name
    Clear-BuildPath $distPath; Clear-BuildPath $workPath
    New-Item -ItemType Directory -Force -Path $distPath,$workPath | Out-Null
    uv run --locked --group build pyinstaller --clean --noconfirm --distpath $distPath --workpath $workPath ("desktop\pyinstaller\"+$name+".spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $name." }
  }
  $gui=Join-Path $dist "gui\BondSealGUI"; $cli=Join-Path $dist "cli\BondSealCLI"
  $worker=Join-Path $dist "worker\BondSealWordWorker.exe"; $dll=Join-Path $artifactRoot "native\BondSealContextMenu.dll"
  foreach ($p in @($gui,$cli,$worker,$dll)) { if (-not (Test-Path -LiteralPath $p)) { throw "Missing output: $p" } }
  $stage=Join-Path $artifactRoot "package-stage"; Clear-BuildPath $stage
  New-Item -ItemType Directory -Force -Path (Join-Path $stage "GUI"),(Join-Path $stage "CLI"),(Join-Path $stage "windows"),(Join-Path $stage "Assets") | Out-Null
  Copy-Item -Path (Join-Path $gui "*") -Destination (Join-Path $stage "GUI") -Recurse -Force
  Copy-Item -Path (Join-Path $cli "*") -Destination (Join-Path $stage "CLI") -Recurse -Force
  Copy-Item -LiteralPath $worker -Destination (Join-Path $stage "GUI\BondSealWordWorker.exe")
  Copy-Item -LiteralPath $worker -Destination (Join-Path $stage "CLI\BondSealWordWorker.exe")
  Copy-Item -LiteralPath $dll -Destination (Join-Path $stage "windows\BondSealContextMenu.dll")
  Copy-Item -LiteralPath windows\Package.appxmanifest -Destination (Join-Path $stage "AppxManifest.xml")
  uv run --locked python windows\scripts\generate_assets.py
  if ($LASTEXITCODE -ne 0) { throw "Asset generation failed." }
  Copy-Item -Path (Join-Path $artifactRoot "package-assets\*") -Destination (Join-Path $stage "Assets") -Force
  $manifestPath=Join-Path $stage "AppxManifest.xml"; $manifest=[IO.File]::ReadAllText($manifestPath)
  $publisherXml=[Security.SecurityElement]::Escape($Publisher)
  $manifest=$manifest.Replace('Publisher="CN=BondSealPages-Development"','Publisher="'+$publisherXml+'"')
  $manifest=$manifest.Replace('Version="0.1.0.0"','Version="'+$Version+'"')
  [IO.File]::WriteAllText($manifestPath,$manifest,[Text.UTF8Encoding]::new($false))
  $package=Join-Path $artifactRoot "BondSealPages-unsigned.msix"
  if (Test-Path -LiteralPath $package) { Remove-Item -LiteralPath $package -Force }
  & $makeAppx pack /d $stage /p $package /o
  if ($LASTEXITCODE -ne 0) { throw "MakeAppx failed." }
  if ($SignForInternalTest) {
    if (-not $signTool) { throw "Windows SDK SignTool.exe is required." }
    $certPath=Join-Path $artifactRoot "BondSealPages-InternalTest.cer"
    $cert=New-SelfSignedCertificate -Type Custom -Subject $Publisher -CertStoreLocation "Cert:\CurrentUser\My" -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 -KeyUsage DigitalSignature -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3") -NotAfter (Get-Date).AddYears(2)
    Export-Certificate -Cert $cert -FilePath $certPath | Out-Null
    Import-Certificate -FilePath $certPath -CertStoreLocation "Cert:\CurrentUser\TrustedPeople" | Out-Null
    & $signTool sign /fd SHA256 /sha1 $cert.Thumbprint /s My $package
    if ($LASTEXITCODE -ne 0) { throw "Internal test signing failed." }
    & $signTool verify /pa /v $package
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed." }
    $internalPackage=Join-Path $artifactRoot "BondSealPages-internal-test.msix"
    if (Test-Path -LiteralPath $internalPackage) { Remove-Item -LiteralPath $internalPackage -Force }
    Move-Item -LiteralPath $package -Destination $internalPackage
    $package=$internalPackage
    [IO.File]::WriteAllText((Join-Path $artifactRoot "BondSealPages-InternalTest-Fingerprint.txt"),$cert.Thumbprint,[Text.UTF8Encoding]::new($false))
    Write-Host "Internal test certificate thumbprint: $($cert.Thumbprint)"
  }
  Write-Warning "Development/test signatures are only for internal verification, never public release."
  Write-Host "Package: $package"
}
finally { Pop-Location }
