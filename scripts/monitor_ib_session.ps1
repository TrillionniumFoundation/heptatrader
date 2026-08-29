param([int]$Tail=200)
$dir='D:\quant\HeptaTrader-master\x64\Release\runtime-logs'
$f=Get-ChildItem $dir -Filter 'oms_journal_*.jsonl' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if(-not $f){ Write-Host 'No oms journal found.'; exit 1 }
$events=Get-Content $f.FullName -Tail $Tail | ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } | Where-Object { $_ -ne $null }
$intents=@($events | Where-Object {$_.event -eq 'order_intent'})
$fills=@($events | Where-Object {$_.event -eq 'status' -and $_.status -eq 'Filled'})
$byStrategy = $fills | Group-Object strategy | ForEach-Object { "{0}: {1}" -f $_.Name, $_.Count }
Write-Host "OMS_FILE=$($f.FullName)"
Write-Host "INTENTS=$($intents.Count) FILLS=$($fills.Count)"
Write-Host "FILLS_BY_STRATEGY="
$byStrategy | ForEach-Object { Write-Host "  $_" }
