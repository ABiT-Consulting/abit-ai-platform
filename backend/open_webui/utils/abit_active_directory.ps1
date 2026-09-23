$ErrorActionPreference = 'Stop'

function Write-Result([hashtable]$Value) {
    $Value | ConvertTo-Json -Compress -Depth 5
    exit 0
}

function Get-AttributeFirstValue($Entry, [string]$Name) {
    $attribute = $Entry.Attributes[$Name]
    if ($null -eq $attribute -or $attribute.Count -lt 1) {
        return $null
    }
    return [string]$attribute[0]
}

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

    Add-Type -AssemblyName System.DirectoryServices.Protocols

    $identifier = [System.DirectoryServices.Protocols.LdapDirectoryIdentifier]::new($server, 389, $false, $false)
    $upn = "$username@$domain"
    $credential = [System.Net.NetworkCredential]::new($upn, $password)
    $connection = [System.DirectoryServices.Protocols.LdapConnection]::new(
        $identifier,
        $credential,
        [System.DirectoryServices.Protocols.AuthType]::Negotiate
    )

    try {
        $connection.Timeout = [TimeSpan]::FromSeconds(6)
        $connection.SessionOptions.ProtocolVersion = 3
        $connection.SessionOptions.Signing = $true
        $connection.SessionOptions.Sealing = $true
        $connection.Bind()

        $filter = "(&(objectCategory=person)(objectClass=user)(sAMAccountName=$username))"
        $attributes = [string[]]@(
            'sAMAccountName',
            'userPrincipalName',
            'mail',
            'displayName',
            'cn',
            'memberOf'
        )
        $request = [System.DirectoryServices.Protocols.SearchRequest]::new(
            $searchBase,
            $filter,
            [System.DirectoryServices.Protocols.SearchScope]::Subtree,
            $attributes
        )
        $response = [System.DirectoryServices.Protocols.SearchResponse]$connection.SendRequest($request)

        if ($null -eq $response -or $response.Entries.Count -lt 1) {
            Write-Result @{ ok = $false; code = 'USER_NOT_FOUND' }
        }

        $entry = $response.Entries[0]
        $sam = Get-AttributeFirstValue $entry 'sAMAccountName'
        $directoryUpn = Get-AttributeFirstValue $entry 'userPrincipalName'
        $mail = Get-AttributeFirstValue $entry 'mail'
        $displayName = Get-AttributeFirstValue $entry 'displayName'
        $cn = Get-AttributeFirstValue $entry 'cn'

        if ([string]::IsNullOrWhiteSpace($sam)) { $sam = $username }
        if ([string]::IsNullOrWhiteSpace($directoryUpn)) { $directoryUpn = $upn }
        if ([string]::IsNullOrWhiteSpace($mail)) { $mail = $directoryUpn }
        if ([string]::IsNullOrWhiteSpace($displayName)) { $displayName = $cn }
        if ([string]::IsNullOrWhiteSpace($displayName)) { $displayName = $sam }

        $groups = @()
        $memberOf = $entry.Attributes['memberOf']
        if ($null -ne $memberOf) {
            foreach ($group in $memberOf) {
                if ($null -ne $group) {
                    $groups += [string]$group
                }
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
    finally {
        if ($null -ne $connection) { $connection.Dispose() }
        $password = $null
        $credential = $null
    }
}
catch [System.DirectoryServices.Protocols.LdapException] {
    if ($_.Exception.ErrorCode -eq 49) {
        Write-Result @{ ok = $false; code = 'INVALID_CREDENTIALS' }
    }

    [Console]::Error.WriteLine($_.Exception.GetBaseException().Message)
    Write-Result @{ ok = $false; code = 'AD_UNAVAILABLE' }
}
catch {
    [Console]::Error.WriteLine($_.Exception.GetBaseException().Message)
    Write-Result @{ ok = $false; code = 'AD_UNAVAILABLE' }
}
