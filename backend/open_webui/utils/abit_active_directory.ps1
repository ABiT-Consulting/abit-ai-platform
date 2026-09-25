$ErrorActionPreference = 'Stop'

function Write-Result([hashtable]$Value) {
    $Value | ConvertTo-Json -Compress -Depth 5
    exit 0
}

function Get-FirstPropertyValue($Result, [string]$Name) {
    $values = $Result.Properties[$Name]
    if ($null -eq $values -or $values.Count -lt 1) { return $null }
    return [string]$values[0]
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AbitNativeLogon {
    [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern bool LogonUser(
        string lpszUsername,
        string lpszDomain,
        string lpszPassword,
        int dwLogonType,
        int dwLogonProvider,
        out IntPtr phToken
    );

    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool CloseHandle(IntPtr hObject);
}
"@

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        Write-Result @{ ok = $false; code = 'INVALID_INPUT' }
    }

    $payload = $raw | ConvertFrom-Json
    $server = [string]$payload.server
    $domain = [string]$payload.domain
    $searchBase = [string]$payload.search_base
    $username = [string]$payload.username
    $password = [string]$payload.password

    if ([string]::IsNullOrWhiteSpace($server) -or
        [string]::IsNullOrWhiteSpace($domain) -or
        [string]::IsNullOrWhiteSpace($searchBase) -or
        [string]::IsNullOrWhiteSpace($username) -or
        [string]::IsNullOrEmpty($password) -or
        $username -notmatch '^[A-Za-z0-9._-]{1,64}$') {
        Write-Result @{ ok = $false; code = 'INVALID_INPUT' }
    }

    # Validate credentials through Windows LSA instead of performing an LDAP
    # Negotiate bind against an IP address.  The previous bind could stall on
    # successful credentials because Kerberos/SPN negotiation against the IP
    # did not complete reliably.  UPN + LogonUser validates the same AD account
    # without writing the password to disk or putting it on the command line.
    $upn = "$username@$domain"
    $token = [IntPtr]::Zero
    $LOGON32_LOGON_NETWORK = 3
    $LOGON32_PROVIDER_DEFAULT = 0

    try {
        $validated = [AbitNativeLogon]::LogonUser(
            $upn,
            $null,
            $password,
            $LOGON32_LOGON_NETWORK,
            $LOGON32_PROVIDER_DEFAULT,
            [ref]$token
        )

        if (-not $validated) {
            $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            if ($errorCode -in @(1326, 1330, 1331, 1793, 1909)) {
                Write-Result @{ ok = $false; code = 'INVALID_CREDENTIALS' }
            }
            Write-Result @{ ok = $false; code = 'AD_UNAVAILABLE' }
        }
    }
    finally {
        if ($token -ne [IntPtr]::Zero) {
            [void][AbitNativeLogon]::CloseHandle($token)
        }
    }

    # The machine is domain joined.  Use its normal directory-read identity to
    # fetch profile data after credential validation, avoiding a second user bind.
    $root = New-Object System.DirectoryServices.DirectoryEntry("LDAP://$server/$searchBase")
    $searcher = New-Object System.DirectoryServices.DirectorySearcher($root)
    $searcher.Filter = "(&(objectCategory=person)(objectClass=user)(sAMAccountName=$username))"
    $searcher.SearchScope = [System.DirectoryServices.SearchScope]::Subtree
    $searcher.PageSize = 1
    foreach ($attr in @('sAMAccountName','userPrincipalName','mail','displayName','cn','memberOf')) {
        [void]$searcher.PropertiesToLoad.Add($attr)
    }

    $result = $searcher.FindOne()
    if ($null -eq $result) {
        Write-Result @{ ok = $false; code = 'USER_NOT_FOUND' }
    }

    $sam = Get-FirstPropertyValue $result 'samaccountname'
    $directoryUpn = Get-FirstPropertyValue $result 'userprincipalname'
    $mail = Get-FirstPropertyValue $result 'mail'
    $displayName = Get-FirstPropertyValue $result 'displayname'
    $cn = Get-FirstPropertyValue $result 'cn'

    if ([string]::IsNullOrWhiteSpace($sam)) { $sam = $username }
    if ([string]::IsNullOrWhiteSpace($directoryUpn)) { $directoryUpn = $upn }
    if ([string]::IsNullOrWhiteSpace($mail)) { $mail = $directoryUpn }
    if ([string]::IsNullOrWhiteSpace($displayName)) { $displayName = $cn }
    if ([string]::IsNullOrWhiteSpace($displayName)) { $displayName = $sam }

    $groups = @()
    $memberOf = $result.Properties['memberof']
    if ($null -ne $memberOf) {
        foreach ($group in $memberOf) {
            if ($null -ne $group) { $groups += [string]$group }
        }
    }

    Write-Result @{
        ok = $true
        username = $sam
        upn = $directoryUpn
        email = $mail
        name = $displayName
        group_dns = $groups
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.GetBaseException().Message)
    Write-Result @{ ok = $false; code = 'AD_UNAVAILABLE' }
}
finally {
    $password = $null
}
