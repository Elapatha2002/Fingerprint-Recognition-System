[CmdletBinding()]
param(
    [ValidateSet('status', 'capture')]
    [string]$Action = 'status',
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 15,
    [string]$SdkDirectory = $env:MFS100_SDK_DIR
)

# This helper must be launched by 32-bit Windows PowerShell because the
# MFS100Dll.dll distributed with SDK 9.0.2.5 is a 32-bit native library.
$ErrorActionPreference = 'Stop'
$device = $null
$initialized = $false

function Write-Result([System.Collections.IDictionary]$Value) {
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Compress -Depth 5))
}

try {
    if ([string]::IsNullOrWhiteSpace($SdkDirectory)) {
        $SdkDirectory = 'C:\Program Files\Mantra\MFS100\Driver\MFS100Test'
    }
    $wrapper = Join-Path $SdkDirectory 'MANTRA.MFS100.dll'
    $native = Join-Path $SdkDirectory 'MFS100Dll.dll'
    if (-not (Test-Path -LiteralPath $wrapper) -or
        -not (Test-Path -LiteralPath $native)) {
        throw "MFS100 SDK files were not found in '$SdkDirectory'."
    }
    if ([IntPtr]::Size -ne 4) {
        throw 'The MFS100 capture helper must run in 32-bit Windows PowerShell.'
    }

    Push-Location $SdkDirectory
    try {
        $env:PATH = "$SdkDirectory;$env:PATH"
        Add-Type -Path $wrapper
        $device = New-Object MANTRA.MFS100
        $initCode = $device.Init()
        if ($initCode -ne 0) {
            $message = $device.GetErrorMsg($initCode)
            throw "MFS100 initialization failed ($initCode): $message"
        }
        $initialized = $true
        if (-not $device.IsConnected()) {
            throw 'MFS100 initialized but no connected scanner was reported.'
        }

        $info = $device.GetDeviceInfo()
        $base = [ordered]@{
            ok = $true
            serial = $info.SerialNo
            model = $info.Model
            make = $info.Make
            width = $info.Width
            height = $info.Height
            sdk_version = $device.GetSDKVersion()
        }

        if ($Action -eq 'status') {
            Write-Result $base
            exit 0
        }

        # AutoCapture initializes the scan, waits for a properly placed finger,
        # and stops it after success/timeout. No click in MFS100 Test is needed.
        [MANTRA.FingerData]$finger = New-Object MANTRA.FingerData
        $captureCode = $device.AutoCapture(
            [ref]$finger,
            ($TimeoutSeconds * 1000),
            $false, # preview events are not needed by the bridge
            $true   # require correct finger placement
        )
        if ($captureCode -ne 0) {
            $message = $device.GetErrorMsg($captureCode)
            throw "Fingerprint capture failed ($captureCode): $message"
        }
        if ($null -eq $finger -or $null -eq $finger.FingerImage) {
            throw 'The SDK reported success but returned no fingerprint image.'
        }

        $stream = New-Object System.IO.MemoryStream
        try {
            $finger.FingerImage.Save(
                $stream,
                [System.Drawing.Imaging.ImageFormat]::Png
            )
            $base.image_base64 = [Convert]::ToBase64String($stream.ToArray())
        }
        finally {
            $stream.Dispose()
        }
        $base.width = $finger.FingerImage.Width
        $base.height = $finger.FingerImage.Height
        $base.dpi = if ($finger.Resolution -gt 0) {
            [int][Math]::Round($finger.Resolution)
        } else { 500 }
        $base.quality = $finger.Quality
        $base.nfiq = $finger.Nfiq
        $base.captured_at = [DateTime]::UtcNow.ToString('o')
        $base.capture_id = [Guid]::NewGuid().ToString('N')
        Write-Result $base
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Result ([ordered]@{
        ok = $false
        error = $_.Exception.Message
        error_type = $_.Exception.GetType().Name
    })
    exit 1
}
finally {
    if ($null -ne $device) {
        if ($initialized) {
            try { [void]$device.Uninit() } catch { }
        }
        try { $device.Dispose() } catch { }
    }
}
