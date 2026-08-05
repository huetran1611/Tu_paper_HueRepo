param(
    [string]$Root = "D:\HueTT\prepare for Phd\Soict_2024_D2d2_extend\Tu_paper_HueRepo",
    [string]$InstanceDir = "D:\HueTT\prepare for Phd\Soict_2024_D2d2_extend\Tu_paper_HueRepo\instance_new",
    [string]$Exe = "D:\HueTT\prepare for Phd\Soict_2024_D2d2_extend\Tu_paper_HueRepo\tabubu_TimeDependent_new_instance_static.exe",
    [int]$Attempts = 1,
    [int]$TimeLimitSec = 0,
    [string]$RunLabel = ""
)

$ErrorActionPreference = "Continue"

if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Executable not found: $Exe"
}

$instances = Get-ChildItem -LiteralPath $InstanceDir -Directory | Sort-Object Name
$summary = @()

foreach ($instance in $instances) {
    $prefix = if ([string]::IsNullOrWhiteSpace($RunLabel)) { "" } else { "$RunLabel`_" }
    $logPath = Join-Path $instance.FullName "$($prefix)static_solve_log.txt"
    $solutionPath = Join-Path $instance.FullName "$($prefix)static_solution_best.txt"
    $iterationPath = Join-Path $instance.FullName "$($prefix)static_iteration_mode_0.txt"
    $rootSolutionPath = Join-Path $Root "output_solution_best.txt"
    $rootIterationPath = Join-Path $Root "output_mode_0.txt"

    Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $solutionPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $iterationPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $rootSolutionPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $rootIterationPath -Force -ErrorAction SilentlyContinue

    Push-Location $Root
    try {
        $start = Get-Date
        $args = @($instance.FullName, "--attempts=$Attempts")
        if ($TimeLimitSec -gt 0) {
            $args += "--time-limit=$TimeLimitSec"
        }
        & $Exe @args *> $logPath
        $exitCode = $LASTEXITCODE
        $elapsed = ((Get-Date) - $start).TotalSeconds

        if (Test-Path -LiteralPath $rootSolutionPath) {
            Move-Item -LiteralPath $rootSolutionPath -Destination $solutionPath -Force
        }
        if (Test-Path -LiteralPath $rootIterationPath) {
            Move-Item -LiteralPath $rootIterationPath -Destination $iterationPath -Force
        }

        $improved = $null
        $feasible = $null
        if (Test-Path -LiteralPath $solutionPath) {
            foreach ($line in Get-Content -LiteralPath $solutionPath -TotalCount 10) {
                if ($line -match "^Improved solution cost:\s+(.+)$") { $improved = [double]$Matches[1] }
                if ($line -match "^Final solution feasibility:\s+(.+)$") { $feasible = $Matches[1].Trim() }
            }
        }

        $summary += [PSCustomObject]@{
            Instance = $instance.Name
            ExitCode = $exitCode
            ImprovedMakespan = $improved
            Feasibility = $feasible
            ElapsedSec = [Math]::Round($elapsed, 3)
            Log = $logPath
            Solution = $solutionPath
        }
    }
    finally {
        Pop-Location
    }
}

$summaryName = if ([string]::IsNullOrWhiteSpace($RunLabel)) { "static_solve_summary.csv" } else { "$RunLabel`_static_solve_summary.csv" }
$summaryPath = Join-Path $InstanceDir $summaryName
$summary | Export-Csv -LiteralPath $summaryPath -NoTypeInformation
$summary | Format-Table -AutoSize
Write-Host "Static summary written to $summaryPath"
