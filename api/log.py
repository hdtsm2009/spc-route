"""Vercel Serverless Function: 利用ログ。
POST /api/log          … イベントを記録（searchイベント等。フロントから）
GET  /api/log?days=N   … エクスポート（Authorization: Bearer <LOG_TOKEN> 必須・最大180日）

設計: _docs/設計_利用ログと分析.md（確定版）。KV未設定時はPOSTは黙ってスキップ、GETは無効。
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import _eventlog
try:
    import _kv
except Exception:
    _kv = None

ALLOWED_EV = {"search", "route", "visit"}


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return
        if body.get("ev") not in ALLOWED_EV:
            self._json(400, {"error": "invalid ev"})
            return
        # サーバ側の信頼境界：t/schema_v/model_version は _eventlog が付与
        _eventlog.log_event(body)
        self._json(200, {"ok": True})

    def do_GET(self):
        token = os.environ.get("LOG_TOKEN", "").strip()
        auth = self.headers.get("Authorization", "")
        if not token:
            self._json(403, {"error": "export disabled (LOG_TOKEN未設定)"})
            return
        if auth != f"Bearer {token}":
            self._json(403, {"error": "forbidden"})
            return
        if not _kv or not _kv.is_configured():
            self._json(200, {"events": [], "configured": False})
            return
        # days（最大180）
        days = 30
        q = self.path.split("?", 1)
        if len(q) == 2:
            for kv in q[1].split("&"):
                if kv.startswith("days="):
                    try:
                        days = int(kv[5:])
                    except ValueError:
                        pass
        days = max(1, min(180, days))
        import datetime
        out = []
        base = _eventlog.now_jst()
        try:
            for i in range(days):
                key = "ev:" + (base - datetime.timedelta(days=i)).strftime("%Y%m%d")
                for raw in (_kv._cmd("LRANGE", key, 0, -1) or []):
                    try:
                        out.append(json.loads(raw))
                    except Exception:
                        pass
        except Exception as e:
            self._json(200, {"events": out, "warn": str(e)})
            return
        self._json(200, {"events": out, "count": len(out), "days": days})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, status, obj):
        enc = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(enc)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(enc)

    def log_message(self, fmt, *args):
        pass
