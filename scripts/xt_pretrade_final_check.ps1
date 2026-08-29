param([string]$ProjectRoot='D:\quant\HeptaTrader-master')
$ErrorActionPreference='Stop'
$ts=Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir=Join-Path $ProjectRoot "runtime-logs\xt-pretrade-final-check-$ts"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$checks=@()
function Add-Check($name,$pass,$detail){ $script:checks += [pscustomobject]@{name=$name;pass=[bool]$pass;detail=[string]$detail} }

$qmt=Get-Process -ErrorAction SilentlyContinue | ? { $_.ProcessName -match 'XtMiniQmt|XtItClient|QMT|xt' }
$qd='not found'
if($qmt){ $qd=($qmt|select -First 3|%{"$($_.ProcessName):$($_.Id)"}) -join ',' }
Add-Check 'QMT_PROCESS_ONLINE' ([bool]$qmt) $qd

$req=@('D:\国金证券QMT交易端\userdata','D:\quant\HeptaTrader-master\x64\Release\HeptaTrader.exe','D:\quant\HeptaTrader-master\docs\XT-CUTOVER-CHECKLIST.md')
foreach($p in $req){ Add-Check ("PATH_EXISTS::"+$p) (Test-Path $p) '' }

$allowXt=$env:HEPTA_ALLOW_XT_ORDERS
$gk=$env:HEPTA_GLOBAL_KILL_SWITCH
$fo=$env:HEPTA_FLATTEN_ONLY
if([string]::IsNullOrEmpty($allowXt)){$allowXt='<null>'}
if([string]::IsNullOrEmpty($gk)){$gk='<null>'}
if([string]::IsNullOrEmpty($fo)){$fo='<null>'}
Add-Check 'SAFE_FLAG_HEPTA_ALLOW_XT_ORDERS' ($allowXt -ne '1') ("current="+$allowXt)
Add-Check 'SAFE_FLAG_GLOBAL_KILL_OR_FLATTEN_VISIBLE' $true ("GLOBAL_KILL="+$gk+"; FLATTEN_ONLY="+$fo)

$smokeOut=Join-Path $outDir 'smoke.stdout.log'
$smokeErr=Join-Path $outDir 'smoke.stderr.log'
$p=Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"$ProjectRoot\scripts\run_xt_scaffold_smoke.ps1",'-ProjectRoot',$ProjectRoot,'-ExePath',"$ProjectRoot\x64\Release\HeptaTrader.exe",'-WorkDir',"$ProjectRoot\x64\Release",'-RunSec','8') -PassThru -RedirectStandardOutput $smokeOut -RedirectStandardError $smokeErr
$p.WaitForExit()
$st=''
if(Test-Path $smokeOut){$st=Get-Content $smokeOut -Raw -ErrorAction SilentlyContinue}
$sp=([int]$p.ExitCode -eq 0) -and ($st -match 'OVERALL=PASS')
Add-Check 'XT_SCAFFOLD_SMOKE' $sp ("exit="+[int]$p.ExitCode)

$overall = (($checks|?{-not $_.pass}).Count -eq 0)
$summary=Join-Path $outDir 'summary.txt'
$json=Join-Path $outDir 'summary.json'
$lines=@("OVERALL="+($(if($overall){'PASS'}else{'FAIL'})),"OUT_DIR=$outDir")
$lines += ($checks|%{"[$($_.name)] "+($(if($_.pass){'PASS'}else{'FAIL'}))+" :: "+$_.detail})
$lines|Set-Content $summary -Encoding UTF8
($checks|ConvertTo-Json -Depth 4)|Set-Content $json -Encoding UTF8
Write-Output ("OVERALL="+($(if($overall){'PASS'}else{'FAIL'})))
Write-Output ("SUMMARY="+$summary)
Write-Output ("JSON="+$json)
if($overall){exit 0}else{exit 1}
