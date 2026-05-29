"""Vercel Serverless Function: plan
GET /api/plan?id=YYYYMMDD_HHMMSS_NNNN

KVに保存されたプランHTMLスナップショットを返す（共有用パーマリンク）。
存在しない / 期限切れ（30日）の場合は 404 メッセージHTML。
"""
from http.server import BaseHTTPRequestHandler
import urllib.parse

try:
    import _kv  # type: ignore
except Exception:  # noqa: BLE001
    _kv = None

_NOT_FOUND_HTML = """<!DOCTYPE html><html lang="ja"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>プランが見つかりません</title>
<style>body{{font-family:'Hiragino Sans','Meiryo',sans-serif;background:#f5f6fa;color:#333;
text-align:center;padding:60px 20px}}.box{{max-width:420px;margin:0 auto;background:#fff;
border-radius:8px;padding:36px 24px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
h1{{font-size:20px;margin:0 0 12px}}p{{color:#666;font-size:13px;line-height:1.7}}
a{{display:inline-block;margin-top:18px;padding:9px 20px;background:#2c3e50;color:#fff;
border-radius:4px;text-decoration:none;font-size:13px}}</style></head>
<body><div class="box"><h1>🔍 {title}</h1>
<p>{msg}</p>
<a href="/">一覧に戻る</a></div></body></html>"""


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        pid = (params.get("id", [""])[0] or "").strip()

        if not pid:
            self._html(400, _NOT_FOUND_HTML.format(
                title="IDが指定されていません",
                msg="URLに ?id=... が必要です。"))
            return

        if _kv is None or not _kv.is_configured():
            self._html(503, _NOT_FOUND_HTML.format(
                title="保存機能が無効です",
                msg="このサーバーではプラン保存（KV）が設定されていません。"))
            return

        try:
            html = _kv.kv_get(f"plan:{pid}")
        except Exception:
            html = None

        if not html:
            self._html(404, _NOT_FOUND_HTML.format(
                title="プランが見つかりません",
                msg="このプランは存在しないか、保存期限（30日）が過ぎて削除されました。"))
            return

        self._html(200, html)

    def _html(self, status: int, body: str):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        pass
