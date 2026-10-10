$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } elseif ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { throw 'Set ANDROID_SDK_ROOT or ANDROID_HOME before building.' }
$javaHome = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { throw 'Set JAVA_HOME before building.' }
$candidates = @('R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z')
$workDrive = $candidates | Where-Object { -not (Get-PSDrive -Name $_ -ErrorAction SilentlyContinue) } | Select-Object -First 1
if (-not $workDrive) { throw 'No free drive letter is available for the temporary build mapping.' }
$drive = "${workDrive}:"
& subst $drive $root
if ($LASTEXITCODE -ne 0) { throw 'Could not create a temporary ASCII-path mapping for the project.' }

try {
    $workRoot = "$drive\"
    $out = Join-Path $workRoot 'build'
    New-Item -ItemType Directory -Force $out, (Join-Path $out 'compiled'), (Join-Path $out 'dex') | Out-Null

    $resInput = Join-Path $out 'res-input'
    Remove-Item $resInput -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $resInput | Out-Null
    # aapt2 cannot traverse this OneDrive reparse-point path directly.
    Copy-Item (Join-Path $workRoot 'res\*') $resInput -Recurse -Force
    & "$sdk\build-tools\36.0.0\aapt2.exe" compile --dir $resInput -o (Join-Path $out 'res.zip')
    if ($LASTEXITCODE -ne 0) { throw 'aapt2 resource compilation failed.' }

    & "$sdk\build-tools\36.0.0\aapt2.exe" link -I "$sdk\platforms\android-34\android.jar" --manifest (Join-Path $workRoot 'AndroidManifest.xml') -R (Join-Path $out 'res.zip') -o (Join-Path $out 'phone-ai-unsigned.apk') --auto-add-overlay --min-sdk-version 24 --target-sdk-version 35
    if ($LASTEXITCODE -ne 0) { throw 'aapt2 manifest linking failed.' }

    $sources = Get-ChildItem (Join-Path $workRoot 'src') -Recurse -Filter *.java | ForEach-Object FullName
    & (Join-Path $javaHome 'bin\javac.exe') -encoding UTF-8 -source 8 -target 8 -classpath "$sdk\platforms\android-34\android.jar" -d (Join-Path $out 'classes') $sources
    if ($LASTEXITCODE -ne 0) { throw 'javac compilation failed.' }

    $classFiles = Get-ChildItem (Join-Path $out 'classes') -Recurse -Filter *.class | ForEach-Object FullName
    & "$sdk\build-tools\36.0.0\d8.bat" --lib "$sdk\platforms\android-34\android.jar" --output (Join-Path $out 'dex') $classFiles
    if ($LASTEXITCODE -ne 0) { throw 'D8 dex compilation failed.' }
    Copy-Item (Join-Path $out 'dex\classes.dex') (Join-Path $out 'compiled\classes.dex') -Force

    $tmp = Join-Path $out 'apk'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    $zipInput = Join-Path $out 'phone-ai-input.zip'
    Copy-Item (Join-Path $out 'phone-ai-unsigned.apk') $zipInput -Force
    Expand-Archive -Path $zipInput -DestinationPath $tmp -Force
    Copy-Item (Join-Path $out 'compiled\classes.dex') (Join-Path $tmp 'classes.dex') -Force
    Remove-Item (Join-Path $out 'phone-ai-unsigned.apk') -Force
    & (Join-Path $javaHome 'bin\jar.exe') c0f (Join-Path $out 'phone-ai-unsigned.apk') -C $tmp .
    if ($LASTEXITCODE -ne 0) { throw 'APK assembly failed.' }

    $keystore = Join-Path $env:USERPROFILE '.android\debug.keystore'
    $signedApk = Join-Path $out 'phone-ai.apk'
    $alignedApk = Join-Path $out 'phone-ai-aligned.apk'
    & "$sdk\build-tools\36.0.0\zipalign.exe" -f -p 4 (Join-Path $out 'phone-ai-unsigned.apk') $alignedApk
    if ($LASTEXITCODE -ne 0) { throw 'APK alignment failed.' }
    & "$sdk\build-tools\36.0.0\apksigner.bat" sign --ks $keystore --ks-pass pass:android --out $signedApk $alignedApk
    if ($LASTEXITCODE -ne 0) { throw 'APK signing failed.' }
    & "$sdk\build-tools\36.0.0\apksigner.bat" verify $signedApk
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
    $release = Join-Path $root 'release'
    New-Item -ItemType Directory -Force $release | Out-Null
    Copy-Item $signedApk (Join-Path $release 'PhoneAI-0.4.5-signed.apk') -Force
    Write-Host (Join-Path $root 'build\phone-ai.apk')
}
finally {
    & subst $drive /d
}
