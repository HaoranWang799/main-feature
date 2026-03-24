"""
本地开发服务器 — 同时提供静态文件 + API 代理
用法: python server.py
然后浏览器打开 http://localhost:8080
"""

import http.server
import json
import os
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
