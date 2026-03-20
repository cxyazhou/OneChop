#!/usr/bin/env pwsh
# Retry pushing local main to origin until successful.
Param(
  [string]$Remote = "origin",
  [string]$Branch = "main",
  [int]$InitialDelay = 30
)
$delay = $InitialDelay
Write-Host "Starting push retry script: pushing $Remote/$Branch"
while ($true) {
  Write-Host "Attempt: git push -u $Remote $Branch"
  git push -u $Remote $Branch
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Push succeeded."
    break
  } else {
    Write-Host "Push failed (exit code $LASTEXITCODE). Retrying in $delay seconds..."
    Start-Sleep -Seconds $delay
    $delay = [math]::Min($delay * 2, 3600)
  }
}
