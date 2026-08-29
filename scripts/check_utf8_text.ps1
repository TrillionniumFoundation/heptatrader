param(
  [string]$ProjectRoot='D:\quant\HeptaTrader-master'
)
$patterns = @('*.ps1','*.md','*.json','*.xml')
$bad = @()
$excludePatterns = @('\Interface\CTPTradeApi32\error.xml','\Interface\CTPTradeApi64\error.xml','\Interface\CTPTradeApiLinux\error.xml','\HeptaTrade\Instrument.xml','\x64\Release\TradingSession.xml')
foreach($pat in $patterns){
  Get-ChildItem -Path $ProjectRoot -Recurse -File -Filter $pat | ForEach-Object {
    $norm = $_.FullName
    if($excludePatterns | Where-Object { $norm.EndsWith($_) }){ return }
    try {
      $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
      [void][System.Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    } catch {
      $bad += $_.FullName
    }
  }
}
if($bad.Count -gt 0){
  Write-Host "NON_UTF8_COUNT=$($bad.Count)"
  $bad | Select-Object -First 50 | ForEach-Object { Write-Host $_ }
  exit 1
}
Write-Host 'UTF8_CHECK=PASS'
