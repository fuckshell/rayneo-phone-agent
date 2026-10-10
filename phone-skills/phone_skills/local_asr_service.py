"""Persistent local speech recognition service for Phone AI.

The service only listens on loopback.  Keeping the model in a dedicated
Python 3.10 process isolates faster-whisper's native dependencies from the
dependency-free audio bridge.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


_model = None
_model_lock = threading.Lock()
_model_info: dict[str, Any] = {"status": "not_loaded"}


def _prepare_cuda_dlls() -> None:
    if os.name != "nt":
        return
    candidates = [
        os.environ.get("PHONE_AI_CUDA_DLL_DIR", ""),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
    ]
    valid = [str(Path(item)) for item in candidates if item and Path(item).is_dir()]
    if valid:
        os.environ["PATH"] = os.pathsep.join(valid + [os.environ.get("PATH", "")])
        for item in valid:
            try:
                os.add_dll_directory(item)
            except (AttributeError, OSError):
                pass


def _load_model():
    global _model, _model_info
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        _prepare_cuda_dlls()
        from faster_whisper import WhisperModel

        model_path = os.environ.get("PHONE_AI_ASR_MODEL", "models/faster-whisper-large-v3")
        if not (Path(model_path) / "model.bin").is_file():
            raise FileNotFoundError(f"local ASR model is incomplete: {model_path}")
        preferred = os.environ.get("PHONE_AI_ASR_DEVICE", "cuda").lower()
        attempts = [(preferred, "float16" if preferred == "cuda" else "int8")]
        if preferred != "cpu":
            attempts.append(("cpu", "int8"))
        errors = []
        for device, compute_type in attempts:
            try:
                candidate = WhisperModel(
                    model_path,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=True,
                )
                _model = candidate
                _model_info = {
                    "status": "ready",
                    "model": "faster-whisper-large-v3",
                    "device": device,
                    "compute_type": compute_type,
                }
                return _model
            except Exception as exc:
                errors.append(f"{device}:{type(exc).__name__}")
        _model_info = {"status": "error", "errors": errors}
        raise RuntimeError(";".join(errors))


def transcribe(wav: bytes) -> dict[str, Any]:
    started = time.monotonic()
    try:
        model = _load_model()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(wav)
            path = handle.name
        try:
            with _model_lock:
                segments, info = model.transcribe(
                    path,
                    language=os.environ.get("PHONE_AI_ASR_LANGUAGE", "zh"),
                    beam_size=5,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    initial_prompt="以下是普通话手机语音指令，可能包含应用名、人名、地名和英文品牌名。",
                )
                text = "".join(segment.text for segment in segments).strip()
            return {
                "status": "ok" if text else "empty",
                "text": text,
                "provider": "faster-whisper",
                "model": "large-v3",
                "language": getattr(info, "language", "zh"),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "runtime": dict(_model_info),
            }
        finally:
            Path(path).unlink(missing_ok=True)
    except Exception as exc:
        return {
            "status": "error",
            "provider": "faster-whisper",
            "model": "large-v3",
            "error_type": type(exc).__name__,
            "detail": str(exc)[:240],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "PhoneLocalASR/1.0"

        def _json(self, code: int, value: dict[str, Any]):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"ok": True, "service": "phone-local-asr", **_model_info})
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/v1/transcribe":
                self._json(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 44 or length > 2_100_000:
                self._json(400, {"error": "invalid_audio_length"})
                return
            self._json(200, transcribe(self.rfile.read(length)))

        def log_message(self, fmt, *args):
            print("[local-asr] " + (fmt % args), flush=True)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 18883):
    server = ThreadingHTTPServer((host, port), make_handler())
    print(json.dumps({"listening": f"http://{host}:{port}"}), flush=True)
    threading.Thread(target=_warm_model, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _warm_model():
    try:
        _load_model()
    except Exception as exc:
        print(f"[local-asr] warmup failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phone AI local faster-whisper service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18883)
    args = parser.parse_args(argv)
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
