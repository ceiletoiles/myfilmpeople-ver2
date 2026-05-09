Param(
    [string]$BaseUrl = 'http://localhost:8000'
)

Write-Output "Running smoke tests against $BaseUrl"

$ErrorActionPreference = 'Stop'

Invoke-WebRequest -Uri "$BaseUrl/" -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri "$BaseUrl/search/?q=tom" -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri "$BaseUrl/person/525/" -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri "$BaseUrl/login/" -UseBasicParsing | Out-Null

Write-Output "Smoke tests passed."
