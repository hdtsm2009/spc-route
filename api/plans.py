"""Vercel Serverless Function: plans
GET /api/plans

KVに保存されたプランのメタ一覧（新しい順、最新100件）をJSONで返す。
KV未設定時は {"configured": false, "plans": []} を返す（index.html側で分岐）。
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

try:
    import _kv  # type: ignore
    _IMPORT_ERR = ""
except Exception as e:  # noqa: BLE001
    _kv = None
    _IMPORT_ERR = str(e)


def _diag():
    """秘密情報は含めず、設定状況のbooleanのみ返す診断用。"""
    return {
        "kv_import_ok": _kv is not None,
        "import_error": _IMPORT_ERR,
        "has_url": bool(os.environ.get("KV_REST_API_URL")
                        or os.environ.get("UPSTASH_REDIS_REST_URL")),
        "has_token": bool(os.environ.get("KV_REST_API_TOKEN")
                          or os.environ.get("UPSTASH_REDIS_REST_TOKEN")),
    }


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if _kv is None or not _kv.is_configured():
            self._json(200, {"configured": False, "plans": [], "diag": _diag()})
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
