param(
    [Parameter(Mandatory=$true)][string]$Candidates,
    [Parameter(Mandatory=$true)][string]$Output,
    [Parameter(Mandatory=$true)][string]$Packager,
    [Parameter(Mandatory=$true)][string]$PayloadValidator
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'Windows runtime is required' }
if (Test-Path $Output) { throw 'Refusing to overwrite smoke workspace' }
New-Item -ItemType Directory -Path $Output | Out-Null
$results = @()

function Get-MatchingProcess($Record) {
    $current = Get-Process -Id $Record.Id -ErrorAction SilentlyContinue
    if ($current -and $current.StartTime.ToUniversalTime().Ticks -eq $Record.StartTicks) {
        return $current
    }
    return $null
}

function Remember-OwnedTree($Owned) {
    # Only live, start-time-matched owned parents may introduce descendants.
    $all = @(Get-CimInstance Win32_Process)
    $live = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($record in @($Owned.Values)) {
        if (Get-MatchingProcess $record) { [void]$live.Add($record.Id) }
    }
    do {
        $added = $false
        foreach ($entry in $all) {
            $childId = [int]$entry.ProcessId
            if ($live.Contains([int]$entry.ParentProcessId) -and -not $Owned.ContainsKey($childId)) {
                $child = Get-Process -Id $childId -ErrorAction SilentlyContinue
                if ($child) {
                    $Owned[$childId] = @{Id=$childId; StartTicks=$child.StartTime.ToUniversalTime().Ticks}
                    [void]$live.Add($childId)
                    $added = $true
                }
            }
        }
    } while ($added)
}

function Cleanup-OwnedTree($Owned) {
    Remember-OwnedTree $Owned
    $forced = @()
    foreach ($record in @($Owned.Values)) {
        $current = Get-MatchingProcess $record
        if ($current -and $current.MainWindowHandle -ne 0) { [void]$current.CloseMainWindow() }
    }
    Start-Sleep -Milliseconds 500
    foreach ($record in @($Owned.Values)) {
        $current = Get-MatchingProcess $record
        if ($current) {
            # Identity is rechecked, including when the onefile parent already exited.
            $current.Kill()
            [void]$current.WaitForExit(5000)
            $forced += $record.Id
        }
    }
    $remaining = @($Owned.Values | Where-Object { Get-MatchingProcess $_ })
    return @{forced_processes=$forced; remaining_processes=@($remaining | ForEach-Object {$_.Id});
             tracked_identity_count=$Owned.Count}
}

foreach ($mode in @('WorldBloom-Studio')) {
    $owned = @{}
    $process = $null
    $failure = $null
    $row = [ordered]@{mode=$mode; status='running'; stage='extract'; authenticode=$null;
        native_window_observed=$false; webview2_renderer_observed=$false; http_home=$null; source_version_verified=$false;
        graceful_shutdown=$false; gui_visual_and_interactive_review='not performed';
        llm_generation='not performed'; cleanup=$null}
    try {
        $folder = Join-Path $Output $mode
        python -B $Packager extract (Join-Path $Candidates "$mode-portable.zip") $folder
        if ($LASTEXITCODE -ne 0) { throw 'Extracted artifact verification failed' }
        $app = Join-Path $folder 'app'
        $before = @(Get-ChildItem -Recurse -File $app | Sort-Object FullName | Get-FileHash -Algorithm SHA256)
        $exe = Join-Path $folder "$mode.exe"
        $row.authenticode = (Get-AuthenticodeSignature $exe).Status.ToString()
        if ($row.authenticode -ne 'NotSigned') { throw 'Unexpected signing state' }
        foreach ($name in @('WORLDBLOOM_APP_ROOT', 'WORLDBLOOM_STUDIO', 'WORLDBLOOM_PYTHON')) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
        $row.stage = 'launch'
        $process = Start-Process -FilePath $exe -WorkingDirectory $folder -PassThru
        $owned[[int]$process.Id] = @{Id=[int]$process.Id; StartTicks=$process.StartTime.ToUniversalTime().Ticks}
        Remember-OwnedTree $owned
        $port = $null
        $row.stage = 'window-and-http-readiness'
        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        do {
            Remember-OwnedTree $owned
            $process.Refresh()
            if ($process.HasExited) { throw 'Executable exited before readiness' }
            $live = @($owned.Values | ForEach-Object { Get-MatchingProcess $_ })
            $ids = @($live | ForEach-Object { $_.Id })
            $windows = @($live | Where-Object {
                $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'WorldBloom'
            })
            $row.native_window_observed = $windows.Count -gt 0
            $row.webview2_renderer_observed = @($live | Where-Object { $_.ProcessName -eq 'msedgewebview2' }).Count -gt 0
            $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
                Where-Object { $_.OwningProcess -in $ids -and $_.LocalAddress -eq '127.0.0.1' })
            foreach ($listener in $listeners) {
                try {
                    $response = Invoke-WebRequest "http://127.0.0.1:$($listener.LocalPort)/version" -TimeoutSec 3
                    if ($response.StatusCode -eq 200 -and $response.Content -match '36bb15c383ed35c615b5505425c905cb2a612eb4') {
                        $port = $listener.LocalPort
                        $row.source_version_verified = $true
                        break
                    }
                } catch { }
            }
            if ($port -and $row.native_window_observed -and $row.webview2_renderer_observed) { break }
            Start-Sleep -Milliseconds 500
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $port -or -not $row.native_window_observed -or -not $row.webview2_renderer_observed) { throw 'HTTP/version, native window, and owned WebView2 renderer were not all observed' }
        $row.stage = 'http-mode'
        $home = Invoke-WebRequest "http://127.0.0.1:$port/" -TimeoutSec 10
        if ($home.StatusCode -ne 200) { throw 'Home response failed' }
        $row.http_home = 200
        if (-not (Test-Path (Join-Path $folder 'runs')) -or -not (Test-Path (Join-Path $folder 'control'))) {
            throw 'Studio did not initialize disposable runtime directories'
        }
        $row.stage = 'graceful-shutdown'
        foreach ($window in $windows) { [void]$window.CloseMainWindow() }
        if (-not $process.WaitForExit(20000) -or $process.ExitCode -ne 0) { throw 'Executable did not close cleanly' }
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'HTTP listener survived shutdown' }
        $after = @(Get-ChildItem -Recurse -File $app | Sort-Object FullName | Get-FileHash -Algorithm SHA256)
        if (($before | ConvertTo-Json -Depth 4 -Compress) -ne ($after | ConvertTo-Json -Depth 4 -Compress)) { throw 'App payload changed during smoke' }
        $row.graceful_shutdown = $true
        $row.stage = 'isolated-payload-cli-ga'
        python -I -B $PayloadValidator --app $app --output (Join-Path $Output "$mode-payload-check")
        if ($LASTEXITCODE -ne 0) { throw 'Isolated payload/CLI/GA validation failed' }
        $row.status = 'passed'
        $row.stage = 'complete'
    } catch {
        $row.status = 'failed'
        $row.error = $_.Exception.Message
        $failure = $_
    } finally {
        try {
            $row.cleanup = Cleanup-OwnedTree $owned
            if ($row.cleanup.forced_processes.Count -gt 0 -or $row.cleanup.remaining_processes.Count -gt 0) {
                $row.status = 'failed'
                $row.graceful_shutdown = $false
                if (-not $failure) { $failure = 'Owned processes did not all exit gracefully' }
            }
        } catch {
            $row.status = 'failed'
            $row.cleanup = @{status='uncertain'; error=$_.Exception.Message}
            $failure = $_
        }
        $results += $row
        ConvertTo-Json -InputObject @($results) -Depth 8 | Set-Content -Encoding utf8 (Join-Path $Output 'smoke-result.json')
    }
    if ($failure) { throw $failure }
}
