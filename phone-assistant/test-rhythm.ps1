$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $root 'build\rhythm-test'
New-Item -ItemType Directory -Force $out | Out-Null
$javaHome = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { throw 'Set JAVA_HOME before running tests.' }
$java = Join-Path $javaHome 'bin'
& (Join-Path $java 'javac.exe') -encoding UTF-8 -d $out `
    (Join-Path $root 'src\com\zeyu\phoneai\assistant\RhythmPolicy.java') `
    (Join-Path $root 'tests\RhythmPolicyTest.java')
if ($LASTEXITCODE -ne 0) { throw 'RhythmPolicy JVM test compilation failed.' }
& (Join-Path $java 'java.exe') -cp $out RhythmPolicyTest
if ($LASTEXITCODE -ne 0) { throw 'RhythmPolicy JVM test failed.' }
$python = Get-Command python -ErrorAction Stop | Select-Object -ExpandProperty Source
& $python -m unittest discover -s (Join-Path $root 'tests') -p 'test_rhythm_android_contract.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Android rhythm contract tests failed.' }
