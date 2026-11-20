param(
  [Parameter(Mandatory=$true)][string]$SourceRoot,
  [Parameter(Mandatory=$true)][string]$BuildRoot,
  [Parameter(Mandatory=$true)][string]$InstallRoot,
  [Parameter(Mandatory=$true)][string]$PatchPath,
  [string]$CMakeExe = 'cmake',
  [string]$Generator = 'Visual Studio 17 2022',
  [int]$Jobs = 8,
  [switch]$Resume
)
$ErrorActionPreference = 'Stop'
function Resolve-BuildPath([string]$Value) {
  if([IO.Path]::IsPathRooted($Value)){return [IO.Path]::GetFullPath($Value)}
  return [IO.Path]::GetFullPath((Join-Path (Get-Location).ProviderPath $Value))
}
$SourceRoot=Resolve-BuildPath $SourceRoot
$BuildRoot=Resolve-BuildPath $BuildRoot
$InstallRoot=Resolve-BuildPath $InstallRoot
$PatchPath=(Resolve-Path -LiteralPath $PatchPath -ErrorAction Stop).Path

$ExpectedCommit = '4ed093a9f06b170bd1564942fa37e1bb907d93e3'
$ExpectedPatch = 'edfbc26d3fd5960e7bafafe1a26120f5538ba48ea3a6bb3d7ba53965f53d4839'
if((Get-FileHash -LiteralPath $PatchPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPatch){throw 'MuJoCo cache patch SHA256 mismatch.'}

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
  & $Program @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

# Use an isolated source/build/install location. This script does not replace
# an official SDK, alter a UE project's loader, or delete an existing checkout.
if (Test-Path $InstallRoot) {
  if (Get-ChildItem -LiteralPath $InstallRoot -Force | Select-Object -First 1) {
    throw "InstallRoot must be empty: $InstallRoot"
  }
}
if (-not (Test-Path $SourceRoot)) {
  Invoke-Checked 'git' @('clone','--depth','1','--branch','4.0.1',
    'https://github.com/google-deepmind/mujoco.git',$SourceRoot)
}
$ActualCommit = (& git -C $SourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $ActualCommit -ne $ExpectedCommit) {
  throw "Expected MuJoCo 4.0.1 commit $ExpectedCommit, found $ActualCommit"
}
if(-not $Resume){
  $Changes = & git -C $SourceRoot status --porcelain
  if ($Changes) { throw 'SourceRoot must be clean; use Resume only for this exact patch.' }
  Invoke-Checked 'git' @('-C',$SourceRoot,'apply','--check',$PatchPath)
  Invoke-Checked 'git' @('-C',$SourceRoot,'apply',$PatchPath)
}
# A temporary index describes pristine upstream + exactly the pinned patch.
# It verifies every tracked source file without changing the checkout's index.
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$IndexPath=Join-Path $BuildRoot ('rmsim-verify-index-'+[Guid]::NewGuid().ToString('N'))
$PreviousIndex=$env:GIT_INDEX_FILE
try {
  $env:GIT_INDEX_FILE=$IndexPath
  Invoke-Checked 'git' @('-C',$SourceRoot,'read-tree','HEAD')
  Invoke-Checked 'git' @('-C',$SourceRoot,'apply','--cached',$PatchPath)
  Invoke-Checked 'git' @('-C',$SourceRoot,'diff','--exit-code','--')
  $Unexpected=& git -C $SourceRoot ls-files --others --exclude-standard
  if($Unexpected){throw 'Unexpected files in the isolated MuJoCo source tree.'}
} finally {
  if($null -eq $PreviousIndex){Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue}else{$env:GIT_INDEX_FILE=$PreviousIndex}
  Remove-Item -LiteralPath $IndexPath -Force -ErrorAction SilentlyContinue
}

Invoke-Checked $CMakeExe @('-S',$SourceRoot,'-B',$BuildRoot,
  '-G',$Generator,'-A','x64',
  '-DMUJOCO_BUILD_EXAMPLES=OFF','-DMUJOCO_BUILD_SIMULATE=OFF',
  '-DMUJOCO_BUILD_STUDIO=OFF','-DMUJOCO_BUILD_TESTS=OFF',
  '-DMUJOCO_TEST_PYTHON_UTIL=OFF','-DMUJOCO_WITH_USD=OFF',
  '-DMUJOCO_USE_FILAMENT=OFF','-DMUJOCO_USE_FILAMENT_MJR_COMPAT=OFF')
Invoke-Checked $CMakeExe @('--build',$BuildRoot,'--config','Release',
  '--target','mujoco','--parallel',"$Jobs")
Invoke-Checked $CMakeExe @('--install',$BuildRoot,'--config','Release','--prefix',$InstallRoot)

# Restrict UE public include paths to C API headers. A full SDK can include
# math/quat.h from rendering dependencies, which conflicts with UE Math/Quat.h
# on a case-insensitive Windows filesystem.
$CAPI = Join-Path $InstallRoot 'c-api\include'
New-Item -ItemType Directory -Force -Path $CAPI | Out-Null
Copy-Item -Recurse -LiteralPath (Join-Path $InstallRoot 'include\mujoco') -Destination $CAPI

$Artifacts = @(Get-ChildItem -LiteralPath $InstallRoot -Recurse -File |
  Where-Object { $_.Name -in @('mujoco.dll','mujoco.lib') } |
  ForEach-Object { @{ path=$_.FullName; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() } })
$Manifest = @{
  upstream='https://github.com/google-deepmind/mujoco'; upstream_commit=$ExpectedCommit;
  upstream_version='4.0.1'; runtime_version_string='4.0.1+rmsim.static-world-flex.3';
  runtime_version_integer=4000001; configuration='Release'; architecture='x64';
  patch_sha256=(Get-FileHash -LiteralPath $PatchPath -Algorithm SHA256).Hash.ToLower();
  artifacts=$Artifacts; built_utc=(Get-Date).ToUniversalTime().ToString('o')
}
$Manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $InstallRoot 'rmsim-build-manifest.json')
Write-Output "Independent custom SDK built at $InstallRoot"
