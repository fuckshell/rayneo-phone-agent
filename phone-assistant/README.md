# Phone Assistant Android client

This directory contains the source for the optional Android companion used by
the phone-agent example. It provides microphone capture, a foreground relay,
an accessibility action surface, and read-only rhythm/location/sleep sync.

The sample is intentionally local-only: the Java client talks to
`127.0.0.1:18880`, which works with `adb reverse`. If a deployment uses a
user-owned relay or tunnel, make that endpoint an explicit build-time change
and review it before installing the APK. No public server, device serial,
signing key, APK, or runtime log is included here.

## Build

Install an Android SDK with build-tools 36.x and platform 34, and a JDK 8+
compatible compiler. Set `ANDROID_SDK_ROOT` (or `ANDROID_HOME`) and
`JAVA_HOME`, then run:

```powershell
./build.ps1
```

The script writes build output under the local `build/` directory. The
resulting APK is unsigned with a project key unless the deployer supplies a
separate signing step; never commit that APK or its keystore.

## Tests

```powershell
./test-rhythm.ps1
```

The Python tests inspect the Android contract and the JVM test covers the
deterministic rhythm policy. Device, root, Health Connect, and location
features require explicit permission on the target phone.
