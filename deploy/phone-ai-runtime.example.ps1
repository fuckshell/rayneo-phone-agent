$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = if ($env:PHONE_AI_PYTHON) { $env:PHONE_AI_PYTHON } else { 'python' }
$skills = Join-Path $root '..\phone-skills'
$logs = Join-Path $root 'runtime-logs'
New-Item -ItemType Directory -Force $logs | Out-Null

# Supply secrets and private endpoints through the process environment or a
# private secret manager. Do not put them in this file.
Push-Location $skills
try {
    & $python -m phone_skills audio-bridge --host 127.0.0.1 --port 18880 `
        --output-dir (Join-Path $root 'audio-sessions') `
        1> (Join-Path $logs 'audio.out.log') `
        2> (Join-Path $logs 'audio.err.log')
} finally {
    Pop-Location
}
