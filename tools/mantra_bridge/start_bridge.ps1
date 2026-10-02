[CmdletBinding()]
param(
    [string]$HostedOrigin = '',
    [int]$Port = 8766
)

$ErrorActionPreference = 'Stop'
$bridge = Join-Path $PSScriptRoot 'bridge.py'
$arguments = @($bridge, '--port', $Port)
if (-not [string]::IsNullOrWhiteSpace($HostedOrigin)) {
    $origin = $HostedOrigin.TrimEnd('/')
    if (-not [Uri]::IsWellFormedUriString($origin, [UriKind]::Absolute)) {
        throw "HostedOrigin must be a complete URL such as https://fsd.example.com"
    }
    $arguments += @('--allow-origin', $origin)
}

Write-Host 'Starting the Fingerprint Recognition Mantra MFS100 bridge...'
Write-Host 'Do not close this window while using sensor capture.'
& python @arguments
exit $LASTEXITCODE
