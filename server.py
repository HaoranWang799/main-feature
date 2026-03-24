"""
本地开发服务器 — 同时提供静态文件 + API 代理
用法: python server.py
然后浏览器打开 http://localhost:8080
"""

import http.server
import base64
import json
import os
import re
import unicodedata
import urllib.request
import urllib.error

PORT = int(os.environ.get("PORT", "8080"))
PROXY_ROUTES = {
    "/proxy/sexyvoice/": "https://sexyvoice.ai/api/v1/",
    "/proxy/fish/": "https://api.fish.audio/v1/",
}


class ProxyHandler(http.server.SimpleHTTPRequestHandler):

    def end_headers(self):
        # 本地开发禁用缓存，避免前端脚本改了但浏览器仍然使用旧版本
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/config.js":
            self._serve_config_js()
            return
        # 音频下载代理: /proxy/audio?url=...
        if self.path.startswith("/proxy/audio?"):
            self._proxy_audio()
            return
        route, remote = self._match_proxy()
        if route:
            self._proxy(remote, method="GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/fish/tts":
            self._relay_fish_tts()
            return
        if self.path == "/api/test/fish":
            self._test_fish_tts()
            return
        route, remote = self._match_proxy()
        if route:
            self._proxy(remote, method="POST")
        else:
            self.send_error(404)

    # ---- internal ----

    def _match_proxy(self):
        for prefix, target in PROXY_ROUTES.items():
            if self.path.startswith(prefix):
                remote_url = target + self.path[len(prefix):]
                return prefix, remote_url
        return None, None

    def _proxy(self, remote_url, method="GET"):
        # Read body for POST
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None

        # Build upstream request — forward essential headers
        headers = {
            "User-Agent": "SexyVoiceApp/1.0",
        }
        for key in ("Authorization", "Content-Type", "Accept", "model"):
            val = self.headers.get(key)
            if val:
                headers[key] = val

        print(f"  -> PROXY {method} {remote_url}")
        print(f"     Auth: {headers.get('Authorization', 'NONE')[:20]}...")
        print(f"     Model: {headers.get('model', 'NONE')}")
        print(f"     Body length: {len(body) if body else 0}")

        req = urllib.request.Request(remote_url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                # Forward content-type
                ct = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_header("Content-Type", ct)
                self._cors_headers()
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            ct = e.headers.get("Content-Type", "application/json")
            self.send_header("Content-Type", ct)
            self._cors_headers()
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            msg = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, model")

    def _serve_config_js(self):
        config = {
            "XAI_API_KEY": os.environ.get("XAI_API_KEY", "YOUR_XAI_API_KEY"),
            "XAI_TTS_API_KEY": os.environ.get("XAI_TTS_API_KEY", "YOUR_XAI_TTS_API_KEY"),
        }
        payload = f"window.APP_CONFIG = {json.dumps(config, ensure_ascii=False)};"
        body = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_audio(self):
        """Download audio from a remote URL and relay it back (bypass CORS)."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        audio_url = qs.get("url", [""])[0]
        if not audio_url:
            self.send_error(400, "Missing url parameter")
            return
        print(f"  -> AUDIO PROXY {audio_url[:80]}...")
        try:
            req = urllib.request.Request(audio_url, headers={"User-Agent": "SexyVoiceApp/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = resp.read()
                self.send_response(200)
                ct = resp.headers.get("Content-Type", "audio/wav")
                self.send_header("Content-Type", ct)
                self._cors_headers()
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            msg = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_bytes(self, status, content_type, body, extra_headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            print("Client disconnected before response could be fully written.")
            return False

    def _sanitize_api_token(self, raw_token, field_name):
        token = unicodedata.normalize("NFKC", raw_token or "")
        token = token.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
        token = re.sub(r"\s+", "", token)
        token = re.sub(r"^Bearer", "", token, flags=re.IGNORECASE).strip()
        token = token.strip("\"'`")
        token = "".join(ch for ch in token if 33 <= ord(ch) <= 126)
        if not token:
            raise ValueError(f"Missing {field_name}")
        return token

    def _normalize_fish_error(self, raw_error):
        text = (raw_error or "").strip()
        if "Invalid Token" in text:
            return (
                "Fish API Key 无效。请到 https://fish.audio/app/api-keys 重新创建一把新的 API Key，"
                "不要使用登录密码，也不要带 Bearer 前缀。"
            )
        return text or "Fish Audio request failed"

    def _perform_fish_tts(self, request_data, include_reference_id=True):
        api_key = self._sanitize_api_token(
            request_data.get("apiKey") or request_data.get("api_key") or "",
            "Fish API key",
        )

        model = (request_data.get("model") or "s1").strip() or "s1"
        text = (request_data.get("text") or "API test voice check.").strip() or "API test voice check."
        reference_id = (request_data.get("referenceId") or request_data.get("reference_id") or "").strip()
        payload = {
            "text": text,
            "format": request_data.get("format") or "mp3",
            "normalize": request_data.get("normalize", True),
            "latency": request_data.get("latency") or "balanced",
            "temperature": float(request_data.get("temperature", 1.0)),
        }
        if include_reference_id and reference_id:
            payload["reference_id"] = reference_id

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "User-Agent": "SexyVoiceApp/1.0",
            "Authorization": f"Bearer {api_key}",
            "Accept": "audio/mpeg",
            "Content-Type": "application/json; charset=utf-8",
            "model": model,
        }

        try:
            req = urllib.request.Request(
                "https://api.fish.audio/v1/tts",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                return {
                    "ok": True,
                    "status": resp.status,
                    "content_type": resp.headers.get("Content-Type", "audio/mpeg"),
                    "body": resp.read(),
                    "used_default_voice": not include_reference_id and bool(reference_id),
                }
        except urllib.error.HTTPError as e:
            return {
                "ok": False,
                "status": e.code,
                "content_type": e.headers.get("Content-Type", "application/json"),
                "body": e.read(),
                "used_default_voice": not include_reference_id and bool(reference_id),
            }
        except UnicodeEncodeError as e:
            raise ValueError(f"Fish API key format is invalid: {e}") from e

    def _relay_fish_tts(self):
        try:
            request_data = self._read_json()
            result = self._perform_fish_tts(request_data, include_reference_id=True)
            if (not result["ok"]) and (request_data.get("referenceId") or request_data.get("reference_id")):
                fallback = self._perform_fish_tts(request_data, include_reference_id=False)
                if fallback["ok"]:
                    result = fallback

            if result["ok"]:
                self._send_json(200, {
                    "ok": True,
                    "audio_base64": base64.b64encode(result["body"]).decode("ascii"),
                    "content_type": result["content_type"],
                    "used_default_voice": result["used_default_voice"],
                })
                return

            preview = result["body"].decode("utf-8", errors="ignore")[:300]
            normalized_error = self._normalize_fish_error(preview)
            if normalized_error != (preview or "").strip():
                self._send_json(result["status"], {
                    "ok": False,
                    "error": normalized_error,
                    "used_default_voice": result["used_default_voice"],
                })
                return

            self._send_bytes(result["status"], result["content_type"], result["body"])
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            print("Client disconnected during Fish relay.")
        except Exception as e:
            self._send_json(502, {"ok": False, "error": str(e)})

    def _test_fish_tts(self):
        try:
            request_data = self._read_json()
            result = self._perform_fish_tts(request_data, include_reference_id=True)
            if (not result["ok"]) and (request_data.get("referenceId") or request_data.get("reference_id")):
                fallback = self._perform_fish_tts(request_data, include_reference_id=False)
                if fallback["ok"]:
                    result = fallback

            if result["ok"]:
                self._send_json(200, {
                    "ok": True,
                    "status": result["status"],
                    "bytes": len(result["body"]),
                    "used_default_voice": result["used_default_voice"],
                })
                return

            preview = self._normalize_fish_error(result["body"].decode("utf-8", errors="ignore")[:300])
            self._send_json(result["status"], {
                "ok": False,
                "status": result["status"],
                "error": preview,
                "used_default_voice": result["used_default_voice"],
            })
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._send_json(502, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):
        # 简化日志
        print(f"[{self.log_date_time_string()}] {fmt % args}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with http.server.ThreadingHTTPServer(("", PORT), ProxyHandler) as httpd:
        print(f"服务器已启动: http://localhost:{PORT}")
        print(f"Preview URL: http://localhost:{PORT}")
        print(f"代理路由:")
        for prefix, target in PROXY_ROUTES.items():
            print(f"  {prefix}* -> {target}*")
        httpd.serve_forever()
