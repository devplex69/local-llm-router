<#
    stop-all.ps1 - surgical shutdown of the local LLM stack described by registry.json.

    Everything is read from registry.json next to this script, so it works wherever the folder lives:
      1. proxy.py                      (the front door, registry.port)
      2. every backend's own process   (matched by executable path, or by a command line naming that
                                        backend's server script on that backend's port)

    It never kills a bare python.exe by name, so unrelated Python work survives.

    Kill order matters: the proxy is asked to release its engines first (POST /router/stop), then leftovers
    are terminated by BARE PID. taskkill /T is deliberately never used on the proxy - a tree kill there takes
    the loaded model child down in a way that can leave the GPU allocation half-released.

    Expect TWO PIDs per Python component, not one: a venv python.exe is a small trampoline that launches the
    real interpreter as a child, and it is the CHILD that binds the port. The pair is normal, and killing the
    trampoline takes the listener down with it, so both are matched and killed.

    Usage:  .\stop-all.ps1            stop everything
            .\stop-all.ps1 -Report    list what would be stopped, change nothing
#>
param([switch]$Report)

$ErrorActionPreference = 'SilentlyContinue'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Reg = Get-Content -LiteralPath (Join-Path $Here 'registry.json') -Raw | ConvertFrom-Json
$Ports = @([int]$Reg.port) + @($Reg.backends | ForEach-Object { [int]$_.port })

function Test-BackendProc {
    param($p, $b)
    $cmd  = [string]$p.CommandLine
    $path = [string]$p.ExecutablePath
    $exe  = [string]$b.cmd[0]
    if (-not $exe) { return $false }
    # A server binary named in the registry counts only at its exact recorded path. Matching by file NAME would
    # claim every python.exe on the box, because a venv launcher is literally called python.exe.
    if ($path -and ($path -eq $exe)) { return $true }
    $py = ($p.Name -eq 'python.exe') -or ($p.Name -eq 'pythonw.exe')
    if (-not $py) { return $false }
    # A venv python reports its path as the base interpreter, so identity comes from the command line instead:
    # this backend's server script, on this backend's port.
    $script = [string]$b.cmd[1]
    if (-not $script) { return $false }
    (($cmd -match [regex]::Escape((Split-Path -Leaf $script))) -and ($cmd -match "--port\s*$([int]$b.port)\b"))
}

function Get-StackProcesses {
    $all = Get-CimInstance Win32_Process
    $targets = foreach ($p in $all) {
        $cmd = [string]$p.CommandLine
        # Only the interpreters and server binaries count: a bash or powershell whose command line merely
        # MENTIONS proxy.py is a launcher shell or a status query, not the server, and killing it would take an
        # unrelated terminal down.
        $py = ($p.Name -eq 'python.exe') -or ($p.Name -eq 'pythonw.exe')
        $ours = ($py -and ($cmd -match '[\\/"]proxy\.py'))
        if (-not $ours) {
            foreach ($b in $Reg.backends) { if (Test-BackendProc -p $p -b $b) { $ours = $true; break } }
        }
        if ($ours) { $p }
    }
    # de-duplicate, then newest first: the model child was spawned after the parent, so it dies first
    $targets | Sort-Object ProcessId -Unique | Sort-Object CreationDate -Descending
}

function Get-PortOwners {
    $lines = netstat -ano | Select-String 'LISTENING'
    foreach ($port in $Ports) {
        foreach ($line in $lines) {
            if ($line -match ":$port\s" ) {
                $procId = ($line.ToString().Trim() -split '\s+')[-1]
                if ($procId -match '^\d+$' -and [int]$procId -gt 0) {
                    [pscustomobject]@{ Port = [int]$port; Pid = [int]$procId }
                }
            }
        }
    }
}

function Show-GpuHolders {
    $samples = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples |
               Where-Object { $_.CookedValue -gt 200MB } |
               Sort-Object CookedValue -Descending | Select-Object -First 5
    foreach ($s in $samples) {
        $procId = ($s.InstanceName -split '_')[1]
        $name = (Get-Process -Id $procId).ProcessName
        '   {0,8:N0} MB  pid {1,-7} {2}' -f ($s.CookedValue / 1MB), $procId, $name
    }
    if (-not $samples) { '   (nothing holding more than 200 MB)' }
}

$stack = @(Get-StackProcesses)
$owners = @(Get-PortOwners)

''
'============================================================'
"  STOP LOCAL LLM STACK - port $($Reg.port) and backends $($Ports -join ' / ')"
'============================================================'
''

'Our processes right now:'
if ($stack) {
    foreach ($p in $stack) {
        # a venv python.exe is a trampoline that spawns the real interpreter as a CHILD, so one launch is two
        # PIDs with the same command line and the child holds the port. Both are normal; neither is a stray.
        $note = ''
        if ($p.Name -like 'python*') {
            $note = if ([string]$p.ExecutablePath -like '*\.venv\Scripts\*') { 'venv trampoline (parent)' } else { 'real interpreter (may hold the port)' }
        } elseif ($p.Name -like 'llama-server*') {
            $gb = [math]::Round($p.WorkingSetSize / 1GB, 1)
            $note = if ($gb -gt 2) { "model child, $gb GB working set" } else { 'router parent' }
        }
        '   pid {0,-7} {1,-20} {2}' -f $p.ProcessId, $p.Name, $note
    }
} else { '   (none running)' }

''
'Listening on the stack ports:'
if ($owners) {
    foreach ($o in $owners) {
        $n = (Get-Process -Id $o.Pid).ProcessName
        '   :{0}  pid {1,-7} {2}' -f $o.Port, $o.Pid, $n
    }
} else { '   (no stack port is listening)' }

''
'GPU memory holders (>200 MB):'
Show-GpuHolders

if ($Report) {
    ''
    '-Report was given: nothing was stopped.'
    ''
    exit 0
}

if (-not $stack -and -not $owners) {
    ''
    'Nothing to do - the stack is already down.'
    ''
    exit 0
}

# ---- 1. graceful: let the proxy release its engines itself, so VRAM is freed cleanly
''
'[1/3] asking the proxy to release the engines ...'
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$($Reg.port)/router/stop" -Method Post -TimeoutSec 20 | Out-Null
    '      proxy answered /router/stop'
} catch {
    '      proxy not answering (already down) - skipping'
}
Start-Sleep -Seconds 4

# ---- 2. force: whatever is left, by bare PID
'[2/3] terminating leftovers by bare PID ...'
$killed = 0
foreach ($p in (Get-StackProcesses)) {
    '      killing pid {0,-7} {1}' -f $p.ProcessId, $p.Name
    taskkill /F /PID $p.ProcessId | Out-Null
    $killed++
}
if ($killed -eq 0) { '      none left' }

# ---- 3. last resort: anything still holding one of our ports, but only if it looks like ours
'[3/3] checking the stack ports again ...'
Start-Sleep -Seconds 2
$left = @(Get-PortOwners)
if (-not $left) {
    '      every stack port is free'
} else {
    foreach ($o in $left) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($o.Pid)"
        $ours = @((Get-StackProcesses) | Where-Object { $_.ProcessId -eq $o.Pid }).Count -gt 0
        if ($ours) {
            '      :{0} still held by ours (pid {1} {2}) - killing' -f $o.Port, $o.Pid, $proc.Name
            taskkill /F /PID $o.Pid | Out-Null
        } else {
            '      WARNING  :{0} is held by pid {1} ({2}), which is NOT part of this stack - left alone.' -f $o.Port, $o.Pid, $proc.Name
            '               command line: {0}' -f [string]$proc.CommandLine
        }
    }
}

Start-Sleep -Seconds 3
''
'------------------------------------------------------------'
'  RESULT'
'------------------------------------------------------------'
$still = @(Get-StackProcesses)
if ($still) {
    '  Still alive (kill it by hand, it may be mid-write):'
    foreach ($p in $still) { '     pid {0,-7} {1}' -f $p.ProcessId, $p.Name }
} else {
    '  Stack fully stopped - no proxy, no backend servers.'
}
$smi = & nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>$null
if ($smi) {
    $u, $t = ($smi | Select-Object -First 1) -split ','
    '  GPU: {0} MiB used of {1} MiB  ({2} MiB free)' -f $u.Trim(), $t.Trim(), ([int]$t.Trim() - [int]$u.Trim())
}
''
'GPU memory holders now (>200 MB) - if something big is still here it is not ours:'
Show-GpuHolders
''
"  To start again:  $Here\run-router.bat"
'  Note: a model needs its own free VRAM to load, so any other GPU job must finish first.'
''
