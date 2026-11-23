param(
  [Parameter(Mandatory=$true)][string]$UpstreamRepository,
  [Parameter(Mandatory=$true)][string]$SourceRoot,
  [Parameter(Mandatory=$true)][string]$BuildRoot,
  [Parameter(Mandatory=$true)][string]$InstallRoot,
  [Parameter(Mandatory=$true)][string]$PatchPath,
  [Parameter(Mandatory=$true)][string]$BasePython,
  [Parameter(Mandatory=$true)][string]$ViewerEnvironment,
  [Parameter(Mandatory=$true)][string]$PhysicsDll,
  [string]$CMakeExe = 'cmake',
  [string]$Generator = 'Visual Studio 18 2026',
  [int]$Jobs = 4,
  [switch]$Resume
)
$ErrorActionPreference='Stop'
$ExpectedCommit='4ed093a9f06b170bd1564942fa37e1bb907d93e3'
$ExpectedPatch='6fde8a8010410f5ae0a864f049a74a516c84dff2c60c388ff2b3aabe166d1490'
$ExpectedBaseDll='552b7a8ca56617a4702ac0359b216ffaefa95535a4794728edd2df0300aa2e78'
$ExpectedPhysicsDll='544b51a2a79f5bd10341a977ddba7db22c8639db4f1c8b5d13081363af4019e4'

function Resolve-BuildPath([string]$Value) {
  if([IO.Path]::IsPathRooted($Value)){return [IO.Path]::GetFullPath($Value)}
  return [IO.Path]::GetFullPath((Join-Path (Get-Location).ProviderPath $Value))
}
function Digest([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Assert-Empty([string]$Path) {
  if((Test-Path -LiteralPath $Path) -and (Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1)) {
    throw "Destination must be empty: $Path"
  }
}
function Contains-Path([string]$Parent,[string]$Child) {
  return $Child.Equals($Parent,[StringComparison]::OrdinalIgnoreCase) -or
    $Child.StartsWith($Parent.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)
}
foreach($Name in @('UpstreamRepository','SourceRoot','BuildRoot','InstallRoot','PatchPath','BasePython','ViewerEnvironment','PhysicsDll')) {
  Set-Variable -Name $Name -Value (Resolve-BuildPath (Get-Variable -Name $Name -ValueOnly))
}
$BaseEnvironment=Split-Path -Parent (Split-Path -Parent $BasePython)
$BasePackages=Join-Path $BaseEnvironment 'Lib\site-packages'
$BaseDll=Join-Path $BasePackages 'mujoco\mujoco.dll'
if((Digest $PatchPath) -ne $ExpectedPatch){throw 'Renderer-only patch SHA256 mismatch.'}
if((Digest $BaseDll) -ne $ExpectedBaseDll){throw 'Official base display DLL identity mismatch.'}
if((Digest $PhysicsDll) -ne $ExpectedPhysicsDll){throw 'Protected UE physics DLL identity mismatch.'}
if($Jobs -lt 1 -or $Jobs -gt 32){throw 'Jobs must be between 1 and 32.'}
$Destinations=@($SourceRoot,$BuildRoot,$InstallRoot,$ViewerEnvironment)
if(($Destinations | Select-Object -Unique).Count -ne 4){throw 'Source/build/install/viewer destinations must be distinct.'}
foreach($Path in $Destinations) {
  foreach($Protected in @($BaseEnvironment,$UpstreamRepository,(Split-Path -Parent $PhysicsDll))) {
    if((Contains-Path $Protected $Path) -or (Contains-Path $Path $Protected)) {
      throw 'Overlay destinations must be separate from the official environment, upstream mirror and UE physics SDK.'
    }
  }
  foreach($Other in $Destinations) {
    if($Path -ne $Other -and (Contains-Path $Path $Other)) {
      throw 'Source/build/install/viewer destinations cannot be nested.'
    }
  }
}
Assert-Empty $InstallRoot
Assert-Empty $ViewerEnvironment
if(-not $Resume){Assert-Empty $BuildRoot}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$script:Commands=[Collections.Generic.List[object]]::new()
function Invoke-Checked([string]$Program,[string[]]$Arguments) {
  $Started=(Get-Date).ToUniversalTime().ToString('o')
  & $Program @Arguments
  $Code=$LASTEXITCODE
  $script:Commands.Add(@{program=$Program;arguments=$Arguments;started_utc=$Started;exit_code=$Code;completed_utc=(Get-Date).ToUniversalTime().ToString('o')})
  $script:Commands | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $BuildRoot 'commands.json')
  if($Code -ne 0){throw "$Program failed with exit code $Code"}
}

# Clone committed objects from the local pinned upstream repository. Its dirty
# .3 solver working tree and Git index are never copied or changed.
if(-not (Test-Path -LiteralPath $SourceRoot)) {
  Invoke-Checked 'git' @('clone','--no-hardlinks','--no-checkout',$UpstreamRepository,$SourceRoot)
  Invoke-Checked 'git' @('-C',$SourceRoot,'checkout','--detach',$ExpectedCommit)
}
$ActualCommit=(& git -C $SourceRoot rev-parse HEAD).Trim()
if($LASTEXITCODE -ne 0 -or $ActualCommit -ne $ExpectedCommit){throw 'Expected pinned pristine MuJoCo 4.0.1 commit.'}
if(-not $Resume) {
  $Changes=& git -C $SourceRoot status --porcelain
  if($Changes){throw 'The independent renderer source must be clean before applying its patch.'}
  Invoke-Checked 'git' @('-C',$SourceRoot,'apply','--check',$PatchPath)
  Invoke-Checked 'git' @('-C',$SourceRoot,'apply',$PatchPath)
}
$IndexPath=Join-Path $BuildRoot ('viewer-verify-index-'+[Guid]::NewGuid().ToString('N'))
$PreviousIndex=$env:GIT_INDEX_FILE
try {
  $env:GIT_INDEX_FILE=$IndexPath
  Invoke-Checked 'git' @('-C',$SourceRoot,'read-tree','HEAD')
  Invoke-Checked 'git' @('-C',$SourceRoot,'apply','--cached',$PatchPath)
  Invoke-Checked 'git' @('-C',$SourceRoot,'diff','--exit-code','--')
  $Unexpected=& git -C $SourceRoot ls-files --others --exclude-standard
  if($Unexpected){throw 'Unexpected source files in the independent renderer tree.'}
  $Allowed=@('src/render/classic/CMakeLists.txt','src/render/classic/render_context.c','src/render/classic/render_gl3.c',
    'src/render/classic/render_staticflex_vbo.c','src/render/classic/render_staticflex_vbo.h')
  $Changed=@(& git -C $SourceRoot diff --cached --name-only HEAD)
  if(@($Changed | Where-Object {$_ -notin $Allowed}).Count -or $Changed.Count -ne $Allowed.Count) {
    throw 'Renderer patch must touch exactly its five reviewed classic-renderer files; public ABI/solver files are excluded.'
  }
} finally {
  if($null -eq $PreviousIndex){Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue}else{$env:GIT_INDEX_FILE=$PreviousIndex}
  Remove-Item -LiteralPath $IndexPath -Force -ErrorAction SilentlyContinue
}

Invoke-Checked $CMakeExe @('-S',$SourceRoot,'-B',$BuildRoot,'-G',$Generator,'-A','x64',
  '-DMUJOCO_BUILD_EXAMPLES=OFF','-DMUJOCO_BUILD_SIMULATE=OFF','-DMUJOCO_BUILD_STUDIO=OFF',
  '-DMUJOCO_BUILD_TESTS=OFF','-DMUJOCO_TEST_PYTHON_UTIL=OFF','-DMUJOCO_WITH_USD=OFF',
  '-DMUJOCO_USE_FILAMENT=OFF','-DMUJOCO_USE_FILAMENT_MJR_COMPAT=OFF')
Invoke-Checked $CMakeExe @('--build',$BuildRoot,'--config','Release','--target','mujoco','--parallel',"$Jobs")
Invoke-Checked $CMakeExe @('--install',$BuildRoot,'--config','Release','--prefix',$InstallRoot)
$BuiltDlls=@(Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -Filter 'mujoco.dll')
if($BuiltDlls.Count -ne 1){throw 'Expected exactly one independently installed mujoco.dll.'}
$BuiltDll=$BuiltDlls[0].FullName

# Create correct new venv redirectors/config, then copy the already pinned wheel
# contents. No package download/install or write into the official environment.
Invoke-Checked $BasePython @('-I','-m','venv','--without-pip',$ViewerEnvironment)
$ViewerPackages=Join-Path $ViewerEnvironment 'Lib\site-packages'
Get-ChildItem -LiteralPath $BasePackages -Force | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $ViewerPackages -Recurse -Force
}
$ViewerDll=Join-Path $ViewerPackages 'mujoco\mujoco.dll'
Copy-Item -LiteralPath $BuiltDll -Destination $ViewerDll -Force
$ViewerPython=Join-Path $ViewerEnvironment 'Scripts\python.exe'
$Probe=Join-Path $BuildRoot 'probe-viewer-overlay.py'
@'
from pathlib import Path
import ctypes,hashlib,importlib.metadata as metadata,json,sys
import mujoco,numpy,glfw
prefix=Path(sys.prefix).resolve()
for module in (mujoco,numpy,glfw):
    assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
assert mujoco.mj_version()==4000001
dll=Path(mujoco.__file__).parent/'mujoco.dll'
library=ctypes.CDLL(str(dll))
library.rmsim_mjr_setStaticFlexCacheEnabled.argtypes=[ctypes.c_int]
library.rmsim_mjr_setStaticFlexCacheEnabled.restype=None
library.rmsim_mjr_staticFlexCacheStatus.argtypes=[]
library.rmsim_mjr_staticFlexCacheStatus.restype=ctypes.c_char_p
initial=json.loads(library.rmsim_mjr_staticFlexCacheStatus())
assert initial['schema']=='rmsim_static_flex_vbo_v1' and not initial['enabled']
library.rmsim_mjr_setStaticFlexCacheEnabled(1)
assert json.loads(library.rmsim_mjr_staticFlexCacheStatus())['enabled']
library.rmsim_mjr_setStaticFlexCacheEnabled(0)
result={'python':sys.executable,'prefix':str(prefix),'mujoco_package':mujoco.__file__,
        'dll_path':str(dll),'dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),
        'versions':{name:metadata.version(name) for name in ('mujoco','mujoco-core','numpy','glfw')},
        'cache_api_initial':initial,'glfw_initialized':False,'model_loaded':False,'solver_steps':0}
Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
'@ | Set-Content -Encoding utf8 $Probe
$ProbeReport=Join-Path $BuildRoot 'viewer-overlay-import.json'
Invoke-Checked $ViewerPython @('-I','-B',$Probe,$ProbeReport)
if((Digest $BaseDll) -ne $ExpectedBaseDll -or (Digest $PhysicsDll) -ne $ExpectedPhysicsDll) {
  throw 'Protected base/physics DLL identity changed during the independent build.'
}
$Manifest=@{
  schema='rmsim_viewer_renderer_build_v1';status='build_and_import_pass_gl_equivalence_pending';
  upstream='https://github.com/google-deepmind/mujoco';upstream_commit=$ExpectedCommit;upstream_version='4.0.1';
  renderer_extension='rmsim.viewer-static-flex-vbo.1';public_abi_layout_changed=$false;solver_source_changed=$false;
  source_root=$SourceRoot;build_root=$BuildRoot;install_root=$InstallRoot;patch_sha256=$ExpectedPatch;
  viewer_environment=$ViewerEnvironment;viewer_python=$ViewerPython;viewer_dll_sha256=(Digest $ViewerDll);
  protected_official_dll=@{path=$BaseDll;sha256=(Digest $BaseDll)};
  protected_physics_dll=@{path=$PhysicsDll;sha256=(Digest $PhysicsDll)};
  copied_packages_from=$BasePackages;modified_wheel_file='mujoco/mujoco.dll';
  import_report_path=$ProbeReport;import_report_sha256=(Digest $ProbeReport);
  commands_path=(Join-Path $BuildRoot 'commands.json');built_utc=(Get-Date).ToUniversalTime().ToString('o')
}
$Manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $InstallRoot 'rmsim-viewer-build-manifest.json')
Write-Output "Independent viewer renderer and environment ready for GL equivalence checks: $ViewerEnvironment"
