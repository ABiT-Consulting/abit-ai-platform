# Create a source-only release bundle for the Linux product-relay cutover.
# Never export Windows browser profiles, credentials, tokens, chats, or runtime logs.
[CmdletBinding()]
param(
  [string]$RuntimeRoot = 'C:\ProgramData\ABiT\ChatRelayRuntime',
  [string]$Destination = 'C:\ProgramData\ABiT\LinuxRelayMigration\product-relays-staging'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$linuxSource = Join-Path $repo 'deployment\linux\aibit-relay'
$sourceModules = @(
  'abit_erp_chatgpt_relay.cjs',
  'bigrro_dev02_chatgpt_relay.cjs',
  'chatgpt-web-gateway.mjs',
  'existing_chrome_cdp.cjs',
  'shared_chatgpt_browser_lock.cjs',
  'shared-chatgpt-browser-lock.mjs',
  'relay_input_files.cjs',
  'chatgpt-composer.mjs',
  'chatgpt-message-dom.mjs'
)
$linuxFiles = @(
  'start-product-relay.sh',
  'verify-product-relays.sh',
  'PRODUCT-RELAY-MIGRATION.md'
)
$unitFile = 'abit-chatgpt-product@.service'
$node = 'C:\Program Files\nodejs\node.exe'
if (-not (Test-Path -LiteralPath $node)) { throw 'Node runtime not found for syntax checks' }
if (Test-Path -LiteralPath $Destination) { throw 'Staging directory already exists; refusing to replace it' }
foreach ($name in $sourceModules) {
  if (-not (Test-Path -LiteralPath (Join-Path $RuntimeRoot $name))) { throw "Missing live Windows runtime module: $name" }
}
foreach ($name in $linuxFiles + @($unitFile)) {
  if (-not (Test-Path -LiteralPath (Join-Path $linuxSource $name))) { throw "Missing reviewed Linux source: $name" }
}
New-Item -ItemType Directory -Path (Join-Path $Destination 'service') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Destination 'systemd') -Force | Out-Null
# Protect staged corporate source; never weaken ACLs on active source folders.
& icacls.exe $Destination /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Unable to restrict staging ACL' }
foreach ($name in $sourceModules) {
  $src = Join-Path $RuntimeRoot $name
  $dst = Join-Path (Join-Path $Destination 'service') $name
  Copy-Item -LiteralPath $src -Destination $dst
  & $node --check $dst
  if ($LASTEXITCODE -ne 0) { throw "Node syntax failed for $name" }
}
foreach ($name in $linuxFiles) {
  Copy-Item -LiteralPath (Join-Path $linuxSource $name) -Destination (Join-Path (Join-Path $Destination 'service') $name)
}
Copy-Item -LiteralPath (Join-Path $linuxSource $unitFile) -Destination (Join-Path (Join-Path $Destination 'systemd') $unitFile)
$playwrightPackage = Join-Path $RuntimeRoot 'node_modules\playwright\package.json'
if (-not (Test-Path -LiteralPath $playwrightPackage)) { throw 'Windows Playwright version not found; Linux dependency needs explicit pin' }
$version = (Get-Content -LiteralPath $playwrightPackage -Raw | ConvertFrom-Json).version
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid Playwright version' }
$dependency = [ordered]@{ private = $true; name = 'abit-relay-runtime-dependencies'; version = '1.0.0'; dependencies = @{ playwright = $version } }
$dependency | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path (Join-Path $Destination 'service') 'package.json') -Encoding UTF8
$sha = @(Get-ChildItem -LiteralPath $Destination -Recurse -File | ForEach-Object {
  $relative = $_.FullName.Substring($Destination.Length + 1).Replace('\','/')
  [ordered]@{ file = $relative; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$manifest = [ordered]@{ generated_utc = (Get-Date).ToUniversalTime().ToString('o'); runtime_source = 'Dev-02 active runtime modules'; browser_profile = 'not transferred'; credentials = 'not transferred'; playwright_version = $version; files = $sha }
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $Destination 'manifest.json') -Encoding UTF8
Write-Output ('STAGING=' + $Destination)
Write-Output ('MODULES=' + $sourceModules.Count)
Write-Output ('PLAYWRIGHT_VERSION=' + $version)
Write-Output ('FILES_HASHED=' + $sha.Count)
Write-Output 'NO_CREDENTIALS_OR_BROWSER_STATE_EXPORTED'
