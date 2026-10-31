# Install a dedicated, public-key-only Windows SSH listener for the ABiT Linux reverse relay tunnel.
# Does NOT alter or restart the main OpenSSH service on port 22.
$ErrorActionPreference = 'Stop'
$nssm='C:\nssm-2.24\win64\nssm.exe'
$sshd='C:\Windows\System32\OpenSSH\sshd.exe'
$runtime='C:\ProgramData\ABiT\ChatRelayRuntime'
$service='ABiTLinuxRelaySSH'
$user=Get-LocalUser -Name 'abit_relay_tunnel' -ErrorAction Stop
if(-not $user.Enabled){throw 'Tunnel-only account disabled'}
if(Get-LocalGroupMember -Group Administrators | Where-Object {$_.SID -eq $user.SID}){throw 'Tunnel account must not be admin'}
if(!(Test-Path $nssm) -or !(Test-Path $sshd)){throw 'NSSM or Windows sshd missing'}
$original=Join-Path $runtime 'tunnel-authorization-original-20261031.txt'
$userAuth='C:\Users\abit_relay_tunnel\.ssh\authorized_keys'
if(Test-Path $original){
    $raw=[IO.File]::ReadAllText($original)
    if($raw -notmatch '^restrict,port-forwarding,permitlisten='){throw 'Unexpected saved authorization'}
    [IO.File]::WriteAllText($userAuth,$raw+[Environment]::NewLine,(New-Object Text.UTF8Encoding($false)))
}
$entry=[IO.File]::ReadAllText($userAuth).Trim()
if($entry -notmatch '^restrict,port-forwarding,permitlisten="127.0.0.1:8819",command="cmd.exe /C exit 1" ssh-ed25519 '){throw 'Unrestricted or unexpected key'}
$auth=Join-Path $runtime 'linux-relay-authorized-keys'
$config=Join-Path $runtime 'linux-relay-sshd_config'
if(Test-Path $auth){ if([IO.File]::ReadAllText($auth).Trim() -ne $entry){throw 'Existing isolated auth differs'} }
else { [IO.File]::WriteAllText($auth,$entry+[Environment]::NewLine,(New-Object Text.UTF8Encoding($false))) }
$settings=@"
Port 2226
ListenAddress 192.168.1.100
AddressFamily inet
HostKey C:/ProgramData/ssh/ssh_host_ed25519_key
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
AuthorizedKeysFile C:/ProgramData/ABiT/ChatRelayRuntime/linux-relay-authorized-keys
AllowUsers abit_relay_tunnel
StrictModes yes
PermitTTY no
MaxSessions 0
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding remote
GatewayPorts no
PermitListen 127.0.0.1:8819
AllowStreamLocalForwarding no
PermitUserEnvironment no
LogLevel VERBOSE
"@
if(Test-Path $config){ if([IO.File]::ReadAllText($config).Trim() -ne $settings.Trim()){throw 'Existing dedicated sshd config differs'} }
else { [IO.File]::WriteAllText($config,$settings,(New-Object Text.UTF8Encoding($false))) }
foreach($file in @($auth,$config)){
    & icacls.exe $file /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' "*$($user.SID.Value):R" | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Failed to set SSH config ACL'}
}
& $sshd -t -f $config
if($LASTEXITCODE -ne 0){throw 'Dedicated OpenSSH config invalid'}
# Terminate only the temporary debug sshd, never the primary sshd service.
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'sshd.exe' -and $_.CommandLine -match '(-ddd|\s-d\s)' -and $_.CommandLine -match '2226'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
$diag=Get-NetFirewallRule -DisplayName 'ABiT Linux Relay SSH Diagnostic' -ErrorAction SilentlyContinue
if($diag){$diag | Remove-NetFirewallRule}
if(-not (Get-NetFirewallRule -DisplayName 'ABiT Linux Relay SSH 2226' -ErrorAction SilentlyContinue)){
    New-NetFirewallRule -DisplayName 'ABiT Linux Relay SSH 2226' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 2226 -RemoteAddress 192.168.253.20 -Profile Any | Out-Null
}
$existing=Get-Service $service -ErrorAction SilentlyContinue
if(-not $existing){
    & $nssm install $service $sshd '-D' '-f' $config | Out-Null
    if($LASTEXITCODE -ne 0){throw 'NSSM service installation failed'}
    & $nssm set $service AppDirectory $runtime | Out-Null
    & $nssm set $service AppExit Default Restart | Out-Null
    & $nssm set $service Start SERVICE_AUTO_START | Out-Null
}
if((Get-Service $service).Status -ne 'Running'){Start-Service $service -ErrorAction Stop}
Start-Sleep -Seconds 2
Write-Output ('ISOLATED_SERVICE='+((Get-Service $service).Status))
Write-Output ('ISOLATED_SSH_PORT_READY='+[bool](Get-NetTCPConnection -LocalPort 2226 -State Listen -ErrorAction SilentlyContinue))
Write-Output ('EXISTING_SSH_PORT_READY='+[bool](Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue))
Write-Output ('DEDICATED_ACCOUNT_NONADMIN='+(-not [bool](Get-LocalGroupMember -Group Administrators | Where-Object {$_.SID -eq $user.SID})))
