"""Vercel Serverless Function: plans
GET /api/plans

KVに保存されたプランのメタ一覧（新しい順、最新100件）をJSONで返す。
KV未設定時は {"configured": false, "plans": []} を返す（index.html側で分岐）。
"""
from http.server import BaseHTTPRequestHandler
import json

try:
    import _kv  # type: ignore
except Exception:  # noqa: BLE001
    _kv = None


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if _kv is None or not _kv.is_configured():
            self._json(200, {"configured": False, "plans": []})
            return

        plans = []
        try:
            for raw in _kv.kv_lrange("plans:list", 0, 99):
                try:
                    plans.append(json.loads(raw))
                except Exception:
                    continue
        except Exception:
            self._json(200, {"configured": True, "plans": []})
            return

        self._json(200, {"configured": True, "plans": plans})

    def _json(self, status: int, obj):
        encoded = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        pass
