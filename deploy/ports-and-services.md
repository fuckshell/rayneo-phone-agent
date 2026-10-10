# Example local services and ports

These are interface names used by the phone-agent example. They describe
local bindings only; they are not a promise that every service is enabled.
Bind them to loopback or a private network and add authentication before
exposing anything outside the machine.

| Port | Service | Role |
| ---: | --- | --- |
| 18880 | phone audio/rhythm bridge | PCM upload, health, and rhythm plan sync |
| 18881 | restricted agent gateway | Optional device-agent API; keep private |
| 18882 | phone relay | Local phone action relay; keep private |
| 18883 | local ASR | Speech transcription endpoint |
| 18789 | OpenClaw gateway | Optional local automation endpoint |
| 11434 | Ollama | Optional local model endpoint |

The public repository does not publish the private WeChat ingest/dashboard
ports, Feishu callbacks, FRP server address, OAuth settings, or any personal
calendar endpoint. Those belong in a deployment-specific configuration and
must never be copied into an issue, log, or public commit.

For USB development, reverse only the service needed by the Android client:

```bash
adb reverse tcp:18880 tcp:18880
```

Use a tunnel only when its server, authentication, firewall, and access list
are owned by the operator. The example FRP file in this directory is a
template and intentionally contains placeholders.
