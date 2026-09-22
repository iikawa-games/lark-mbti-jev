$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -m compileall -q feishu_mbti
if ($LASTEXITCODE -ne 0) { throw 'Compile check failed.' }
