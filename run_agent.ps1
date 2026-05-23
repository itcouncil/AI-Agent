$ErrorActionPreference = "Stop"
$BundledPython = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (Test-Path $BundledPython) {
    & $BundledPython "$PSScriptRoot\agent.py"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py "$PSScriptRoot\agent.py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    python "$PSScriptRoot\agent.py"
} else {
    throw "No Python runtime found. Install Python or run with the bundled Codex Python."
}
