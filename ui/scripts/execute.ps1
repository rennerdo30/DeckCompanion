param([string]$Payload)
$ErrorActionPreference = 'Stop'
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json
$start = New-Object Diagnostics.ProcessStartInfo
$start.FileName = $request.binary
# Windows argv quoting: escape backslashes before quotes and the closing quote.
$start.Arguments = (($request.args | ForEach-Object { '"' + ([regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1')) + '"' }) -join ' ')
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$child = New-Object Diagnostics.Process
$child.StartInfo = $start
try {
    $null = $child.Start()
    [Console]::Error.WriteLine('DeckCompanion engine PID: ' + $child.Id)
    $stdout = $child.StandardOutput.ReadToEndAsync()
    $stderr = $child.StandardError.ReadToEndAsync()
    if (-not $child.WaitForExit(45000)) { $child.Kill(); $child.WaitForExit(); exit 124 }
    [Console]::Out.Write($stdout.Result)
    exit $child.ExitCode
} finally { $child.Dispose() }
