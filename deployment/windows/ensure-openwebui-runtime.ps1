$ErrorActionPreference = 'Stop'

$root = 'C:\ProgramData\ABiT\OpenWebUI'
$nssm = 'C:\nssm-2.24\win64\nssm.exe'
$openWebUI = 'C:\Program Files\Python311\Scripts\open-webui.exe'
$dataDir = 'C:\Program Files\Python311\Lib\site-packages\open_webui\data'
$serviceName = 'ABiTOpenWebUI'
$legacyTask = 'OpenWebUI-8888'
$tunnelTask = 'ABiT-OpenWebUI-Tunnel'

foreach ($required in @($nssm, $openWebUI, $dataDir)) {
    if (-not (Test-Path $required)) {
        throw "Required path missing: $required"
    }
}

# Cloudflare's managed ingress targets localhost:8888. Keep IPv6 localhost
# forwarding to the IPv4-only Open WebUI listener so localhost resolution
# cannot produce intermittent origin failures.
Set-Service -Name iphlpsvc -StartupType Automatic
Start-Service -Name iphlpsvc
& netsh interface portproxy delete v6tov4 listenport=8888 listenaddress=::1 2>$null | Out-Null
& netsh interface portproxy add v6tov4 listenport=8888 listenaddress=::1 connectport=8888 connectaddress=127.0.0.1 | Out-Null

# Run Open WebUI as a real Windows service rather than a repeating Scheduled
# Task. NSSM keeps the process supervised and restarts it after unexpected exits.
$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($null -eq $service) {
    & sc.exe create $serviceName binPath= $nssm start= auto DisplayName= 'ABiT Open WebUI 8888' | Out-Null
}

$parameters = "HKLM\SYSTEM\CurrentControlSet\Services\$serviceName\Parameters"
& reg.exe add $parameters /v Application /t REG_EXPAND_SZ /d $openWebUI /f | Out-Null
& reg.exe add $parameters /v AppParameters /t REG_EXPAND_SZ /d 'serve --host 127.0.0.1 --port 8888' /f | Out-Null
& reg.exe add $parameters /v AppDirectory /t REG_EXPAND_SZ /d 'C:\Windows\System32' /f | Out-Null
& reg.exe add $parameters /v AppEnvironmentExtra /t REG_MULTI_SZ /d "DATA_DIR=$dataDir" /f | Out-Null
& reg.exe add $parameters /v AppStdout /t REG_EXPAND_SZ /d (Join-Path $root 'nssm-out.log') /f | Out-Null
& reg.exe add $parameters /v AppStderr /t REG_EXPAND_SZ /d (Join-Path $root 'nssm-err.log') /f | Out-Null
& reg.exe add "$parameters\AppExit" /ve /t REG_SZ /d Restart /f | Out-Null
& reg.exe add $parameters /v AppRestartDelay /t REG_DWORD /d 5000 /f | Out-Null
& sc.exe failure $serviceName reset= 0 actions= restart/5000/restart/5000/restart/5000 | Out-Null
& sc.exe failureflag $serviceName 1 | Out-Null

# Leave the old task present for rollback, but disable it so it cannot compete
# for port 8888 with the supervised service.
$oldTask = Get-ScheduledTask -TaskName $legacyTask -ErrorAction SilentlyContinue
if ($null -ne $oldTask) {
    try { Stop-ScheduledTask -TaskName $legacyTask -ErrorAction SilentlyContinue } catch {}
    Disable-ScheduledTask -TaskName $legacyTask | Out-Null
}

$service = Get-Service -Name $serviceName
if ($service.Status -ne 'Running') {
    Start-Service -Name $serviceName
}

$deadline = (Get-Date).AddMinutes(3)
$appReady = $false
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8888/health' -TimeoutSec 3
        if ($health.status -eq $true) {
            $appReady = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $appReady) {
    throw 'Open WebUI service did not become healthy on 127.0.0.1:8888'
}

# The existing tunnel task is already SYSTEM-owned and uses the protected token
# file. Start it only when it is not already running.
$tunnel = Get-ScheduledTask -TaskName $tunnelTask -ErrorAction SilentlyContinue
if ($null -ne $tunnel -and $tunnel.State -ne 'Running') {
    Start-ScheduledTask -TaskName $tunnelTask
}

Write-Output 'ABiT Open WebUI runtime is healthy and supervised by NSSM.'
Get-Service -Name $serviceName | Select-Object Name, Status, StartType | Format-List
if ($null -ne $tunnel) {
    Get-ScheduledTask -TaskName $tunnelTask | Select-Object TaskName, State | Format-List
}
