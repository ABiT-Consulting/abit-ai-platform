# Requires Windows LocalSystem/Administrator. Creates only a non-admin SSH reverse-tunnel identity.
# Use ABIT_LINUX_TUNNEL_PUBLIC_KEY from the authorized Linux deployment, never a private key.
$ErrorActionPreference = 'Stop'
$name = 'abit_relay_tunnel'
$publicKey = [string]$env:ABIT_LINUX_TUNNEL_PUBLIC_KEY
if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [A-Za-z0-9._-]+)?$') {
    throw 'Valid Linux SSH public key required.'
}
$existing = Get-LocalUser -Name $name -ErrorAction SilentlyContinue
if (-not $existing) {
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $randomBytes = New-Object byte[] 48
    $rng.GetBytes($randomBytes)
    $rng.Dispose()
    $random = [Convert]::ToBase64String($randomBytes)
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    $password = ConvertTo-SecureString -String $random -AsPlainText -Force
    $created = New-LocalUser -Name $name -Password $password -PasswordNeverExpires -UserMayNotChangePassword -Description 'ABiT Linux relay SSH reverse tunnel'
    $password.Dispose()
    $random = $null
}
$user = Get-LocalUser -Name $name
if (-not $user.Enabled) { throw 'Tunnel identity is disabled.' }
$adminMembers = Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue
if ($adminMembers | Where-Object { $_.SID -eq $user.SID }) { throw 'Tunnel identity must not be an administrator.' }
$userHome = Join-Path 'C:\Users' $name
$sshDir = Join-Path $userHome '.ssh'
New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
$authorizedKeys = Join-Path $sshDir 'authorized_keys'
$entry = 'restrict,port-forwarding,permitlisten="127.0.0.1:8819",command="cmd.exe /C exit 1" ' + $publicKey
if (Test-Path $authorizedKeys) {
    $old = [IO.File]::ReadAllText($authorizedKeys).Trim()
    if ($old -ne $entry) { throw 'Existing tunnel key differs. Refusing to overwrite.' }
} else {
    [IO.File]::WriteAllText($authorizedKeys, $entry + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
}
# Windows OpenSSH requires no inheritance or broad ACEs on authorized_keys.
$accountSid = $user.SID.Value
& icacls.exe $userHome /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "*${accountSid}:(OI)(CI)M" | Out-Null
& icacls.exe $sshDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "*${accountSid}:(OI)(CI)M" | Out-Null
& icacls.exe $authorizedKeys /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' "*${accountSid}:R" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Authorized-keys ACL setup failed.' }
Write-Output ('TUNNEL_USER_READY=' + $name)
Write-Output ('NON_ADMIN=' + (-not [bool]($adminMembers | Where-Object {$_.SID -eq $user.SID})))
Write-Output ('AUTHORIZED_KEY_PRESENT=' + (Test-Path $authorizedKeys))