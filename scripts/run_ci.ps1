param(
    [ValidateSet("quick", "quality", "test", "full", "live", "soak")]
    [string]$Profile = "quick",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }

& $Python (Join-Path $PSScriptRoot "run_ci.py") --profile $Profile @RemainingArgs
exit $LASTEXITCODE
