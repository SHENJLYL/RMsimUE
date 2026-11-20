param(
 [string]$ModelDirectory='',
 [string]$GeometryDirectory='',
 [switch]$RebuildModel
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python=Join-Path $root 'artifacts/environments/mujoco-4.0.1/Scripts/python.exe'
if(-not (Test-Path -LiteralPath $python)){
 throw 'Create artifacts/environments/mujoco-4.0.1 with python -m venv, then pip install -r tools/mujoco/requirements.txt inside it.'
}
if(-not $ModelDirectory){$ModelDirectory=Join-Path $root 'assets/vehicles/aim_sentry/mujoco/rmuc2027'}
$ModelDirectory=[IO.Path]::GetFullPath($ModelDirectory)
$manifest=Join-Path $ModelDirectory 'manifest.json'
if((Test-Path -LiteralPath $ModelDirectory) -and -not $RebuildModel){
 & $python (Join-Path $root 'tools/mujoco/verify_model.py') --manifest $manifest --preflight-only
 exit $LASTEXITCODE
}
$backup=$null
try{
 if(Test-Path -LiteralPath $ModelDirectory){
  $archive=Join-Path $root 'artifacts/mujoco/model-history'
  New-Item -ItemType Directory -Force -Path $archive | Out-Null
  $backup=Join-Path $archive ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
  Move-Item -LiteralPath $ModelDirectory -Destination $backup
 }
 $argsPrepare=@((Join-Path $root 'tools/mujoco/prepare_model.py'),'--repo',$root,'--output',$ModelDirectory)
 if($GeometryDirectory){$argsPrepare+=@('--geometry-directory',[IO.Path]::GetFullPath($GeometryDirectory))}
 & $python @argsPrepare
 if($LASTEXITCODE -ne 0){throw 'Model preparation failed; partial output is retained.'}
 & $python (Join-Path $root 'tools/mujoco/verify_model.py') --manifest $manifest --preflight-only
 if($LASTEXITCODE -ne 0){throw 'New model preflight failed; inspect the preserved package.'}
 Write-Output ('Prepared MuJoCo physical model: '+$manifest)
 if($backup){Write-Output ('Previous package preserved: '+$backup)}
}catch{
 [Console]::Error.WriteLine($_.Exception.Message)
 if($backup){[Console]::Error.WriteLine('Previous package remains at '+$backup)}
 exit 3
}
