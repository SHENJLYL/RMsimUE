param(
    [Parameter(Mandatory = $true)][string]$Rulebook,
    [string]$EngineRoot = 'E:/simulator-tools/UE_5.8',
    [string]$Python = 'python',
    [string]$PdfToPpm = 'pdftoppm'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$ue = Get-UnrealTools -EngineRoot $EngineRoot
& $Python -B (Join-Path $root 'tools/ui/generate_armor_stickers.py') (Resolve-Path -LiteralPath $Rulebook).Path --pdftoppm $PdfToPpm
if ($LASTEXITCODE -ne 0) { throw "Armor sticker generation failed ($LASTEXITCODE)." }
$script = Join-Path $PSScriptRoot 'import_rmuc_armor_materials.py'
$arguments = @((Quote-UnrealArgument $ue.Project), '-run=pythonscript',
    ('-script=' + (Quote-UnrealArgument $script)), '-unattended', '-nop4', '-NoSplash')
$process = Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
[void]$process.Handle
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "Armor material generation failed ($($process.ExitCode)). See Unreal Editor logs." }
Write-Output 'RMUC armor stickers and materials generated.'
