# Cross-platform-friendly Cursor hook launcher for Windows.
# Always prints valid Cursor JSON so failClosed never blocks on launcher failure.
$ErrorActionPreference = "Continue"
$hookArgs = $args

function Write-Allow {
    Write-Output '{"permission":"allow"}'
    exit 0
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe")
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }
    return $null
}

try {
    $uv = Find-Uv
    if (-not $uv) {
        [Console]::Error.WriteLine("rai-hook: uv not found on PATH; allowing (enforcement inactive)")
        Write-Allow
    }

    $stdin = [Console]::In.ReadToEnd()
    $stdin | & $uv run rai-hook @hookArgs
    if ($LASTEXITCODE -eq 0) {
        exit 0
    }
    [Console]::Error.WriteLine("rai-hook: uv run exited $LASTEXITCODE; allowing")
    Write-Allow
}
catch {
    [Console]::Error.WriteLine("rai-hook: $($_.Exception.Message); allowing")
    Write-Allow
}
