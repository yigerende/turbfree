$ErrorActionPreference = 'SilentlyContinue'

$port = 5001
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe'
$url = "http://127.0.0.1:$port"

function Test-WebUI {
    try {
        $connection = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop
        return [bool]$connection
    } catch {
        return $false
    }
}

if (-not (Test-WebUI)) {
    Start-Process -FilePath $python `
        -ArgumentList 'web.py --port 5001' `
        -WorkingDirectory $project `
        -WindowStyle Hidden
}

for ($i = 0; $i -lt 30; $i++) {
    if (Test-WebUI) {
        Start-Process $url
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

Start-Process $url
