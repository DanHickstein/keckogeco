<#
ELL12 probe - characterize the flattener's Thorlabs ELL12 ND-filter slider.

Run this on the machine the slider is plugged into (currently the Menlo
laptop) and paste the full output back for review. It records everything
the keckogeco driver (keckogeco/drivers/thorlabs_ell12.py) assumes but
could not verify without hardware: the info-reply fields (device type,
travel, pulses/mm) and the true pulse count between adjacent slots.

Pure Windows PowerShell - no Python, no installs. CLOSE THE ELLO SOFTWARE
FIRST: the COM port is exclusive.

Usage (from a normal PowerShell window):

  powershell -ExecutionPolicy Bypass -File ell12_probe.ps1
      lists the COM ports so you can find the Elliptec bus

  powershell -ExecutionPolicy Bypass -File ell12_probe.ps1 -Port COM3
      read-only: info, status, current position (no movement)

  powershell -ExecutionPolicy Bypass -File ell12_probe.ps1 -Port COM3 -Move
      also homes the slider, jogs through all six slots recording the
      reported position at each (the firmware knows its own slot spacing,
      so this measures it directly), then moves back to slot 1

The -Move sequence changes which ND filter is in the beam six times;
run it while that is acceptable. -Address selects the Elliptec bus
address char if the unit is not at the default '0'.
#>
param(
    [string]$Port = "",
    [string]$Address = "0",
    [switch]$Move
)

$ErrorActionPreference = "Stop"

$statusCodes = @{
    0 = "ok"; 1 = "communication timeout"; 2 = "mechanical timeout";
    3 = "command not supported"; 4 = "value out of range"; 5 = "module isolated";
    6 = "module out of isolation"; 7 = "initialization error"; 8 = "thermal error";
    9 = "busy"; 10 = "sensor error"; 11 = "motor error"; 12 = "out of range";
    13 = "over current"
}

if (-not $Port) {
    Write-Host "Available COM ports:"
    [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Re-run with:  -Port COM<n>    (Device Manager > Ports shows which is the Elliptec bus)"
    exit 0
}

$sp = New-Object -TypeName System.IO.Ports.SerialPort -ArgumentList $Port, 9600, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$sp.NewLine = "`r`n"
$sp.ReadTimeout = 6000    # a full-travel home takes a while
$sp.WriteTimeout = 2000

function Send-Ell([string]$cmd) {
    # send <address><cmd>, echo the exchange, return the reply line
    $sp.DiscardInBuffer()
    $sp.Write("$Address$cmd`r`n")
    $reply = ""
    try { $reply = $sp.ReadLine().Trim() } catch { $reply = "<no reply (timeout)>" }
    Write-Host ("  {0,-14} -> {1}" -f "$Address$cmd", $reply)
    return $reply
}

function Parse-Pulses([string]$reply) {
    # <addr>PO<8 hex, 32-bit two's complement> -> signed pulses, else $null
    if ($reply.Length -ge 11 -and $reply.Substring(1, 2) -eq "PO") {
        $value = [int64][Convert]::ToUInt32($reply.Substring(3, 8), 16)
        if ($value -ge 2147483648) { $value -= 4294967296 }
        return $value
    }
    if ($reply.Length -ge 5 -and $reply.Substring(1, 2) -eq "GS") {
        $code = [Convert]::ToInt32($reply.Substring(3, 2), 16)
        $meaning = $statusCodes[$code]
        Write-Host "    (status $code = $meaning instead of a position)"
    }
    return $null
}

try {
    $sp.Open()
    Write-Host "=== ELL12 probe on $Port, bus address '$Address' ($(Get-Date -Format s)) ==="
    Write-Host ""
    Write-Host "--- info (in) ---"
    $info = Send-Ell "in"
    if ($info.Length -ge 33 -and $info.Substring(1, 2) -eq "IN") {
        $type = [Convert]::ToInt32($info.Substring(3, 2), 16)
        $travel = [Convert]::ToInt32($info.Substring(21, 4), 16)
        $pulses = [int64][Convert]::ToUInt32($info.Substring(25, 8), 16)
        Write-Host "    device type : $type (keckogeco driver expects 12 for ELL12)"
        Write-Host "    serial      : $($info.Substring(5, 8))"
        Write-Host "    year / fw   : $($info.Substring(13, 4)) / $($info.Substring(17, 2))"
        Write-Host "    travel      : $travel"
        Write-Host "    pulses field: $pulses"
        Write-Host "    derived pulses/slot (travel * pulses / 5): $([math]::Round($travel * $pulses / 5))"
    } else {
        Write-Host "    (unexpected info reply - wrong port or bus address?)"
    }
    Write-Host ""
    Write-Host "--- status (gs) + position (gp) ---"
    $null = Send-Ell "gs"
    $null = Parse-Pulses (Send-Ell "gp")

    if ($Move) {
        Write-Host ""
        Write-Host "--- movement: home, then jog forward through all six slots ---"
        $positions = @()
        $positions += Parse-Pulses (Send-Ell "ho0")
        for ($slot = 2; $slot -le 6; $slot++) {
            Start-Sleep -Milliseconds 300
            $positions += Parse-Pulses (Send-Ell "fw")
        }
        Write-Host ""
        Write-Host "--- measured slot table ---"
        for ($i = 0; $i -lt $positions.Count; $i++) {
            $pulseText = if ($null -eq $positions[$i]) { "?" } else { $positions[$i] }
            Write-Host ("    slot {0}: {1} pulses" -f ($i + 1), $pulseText)
        }
        $diffs = @()
        for ($i = 1; $i -lt $positions.Count; $i++) {
            if ($null -ne $positions[$i] -and $null -ne $positions[$i - 1]) {
                $diffs += ($positions[$i] - $positions[$i - 1])
            }
        }
        if ($diffs.Count -gt 0) {
            $spacing = ($diffs | Measure-Object -Average).Average
            Write-Host ("    spacing between slots: {0} (avg) - use this as slot_pulses if it" -f [math]::Round($spacing))
            Write-Host "    disagrees with the derived value above"
        }
        Write-Host ""
        Write-Host "--- absolute move back to slot 1 (ma 00000000) ---"
        Start-Sleep -Milliseconds 300
        $null = Parse-Pulses (Send-Ell "ma00000000")
        $null = Send-Ell "gs"
    } else {
        Write-Host ""
        Write-Host "(read-only run; add -Move to measure the slot spacing)"
    }
    Write-Host ""
    Write-Host "=== done - paste everything above back for review ==="
} finally {
    if ($sp.IsOpen) { $sp.Close() }
}
