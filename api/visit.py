"""Vercel Serverless Function: 訪問結果の記録・取得（チーム共有・Vercel KV）。

GET  /api/visit            … 全店舗の訪問記録を {store_id: {...}} で返す（候補画面が接触履歴を重ねる）
POST /api/visit            … 1店舗の訪問結果を記録/更新

KV(Upstash Redis)の HASH `visits` に store_id をフィールド、値はJSON文字列で保存する。
KV未設定時は 200/空 で安全にフォールバック（履歴機能がオフになるだけ）。

POST body (JSON):
  id          : str  必須（店舗ID）
  status      : str  未接触/訪問済/架電済/興味あり/掲載済/NG/再訪候補
  owner       : str  担当者
  memo        : str  任意
  method      : str  任意（飛び込み/電話/メール/紹介）
  next_action : str  任意
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import _kv

HASH_KEY = "visits"

_SIDX = None


def _store_meta(sid):
    """店舗IDから (営業ランク, 勢いスコア) を返す（当時スコアのスナップショット用）。"""
    global _SIDX
    if _SIDX is None:
        try:
            import generate_plan as G
            stores, _ = G._load_data()
            _SIDX = {r.get("店舗ID"): (r.get("営業ランク", ""), G.get_momentum(r)) for r in stores}
        except Exception:
            _SIDX = {}
    return _SIDX.get(sid, ("", 0))


def _today_jst():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y-%m-%d")


def _load_all():
    """HGETALL visits → {id: record dict}。"""
    if not _kv.is_configured():
        return {}
    flat = _kv._cmd("HGETALL", HASH_KEY) or []
    out = {}
    for i in range(0, len(flat) - 1, 2):
        try:
            out[flat[i]] = json.loads(flat[i + 1])
        except (ValueError, TypeError):
            pass
    return out


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            self._json(200, {"visits": _load_all()})
        except Exception as e:
            self._json(200, {"visits": {}, "warn": f"KV読込不可: {e}"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return

        sid = str(body.get("id", "")).strip()
        if not sid:
            self._json(400, {"error": "id は必須です"})
            return
        if not _kv.is_configured():
            self._json(503, {"error": "KV未設定のため記録できません（環境変数を確認）"})
            return

        # 変更前のstatus（prev_status・ログ用）
        prev_status = ""
        try:
            cur = _kv._cmd("HGET", HASH_KEY, sid)
            if cur:
                prev_status = (json.loads(cur) or {}).get("status", "")
        except Exception:
            pass

        rec = {
            "status":      body.get("status", ""),
            "owner":       body.get("owner", ""),
            "memo":        body.get("memo", ""),
            "method":      body.get("method", ""),
            "next_action": body.get("next_action", ""),
            "date":        body.get("date") or _today_jst(),
        }
        # status空＝記録の取り消し（フィールド削除）
        try:
            if not rec["status"]:
                _kv._cmd("HDEL", HASH_KEY, sid)
                rec = None
            else:
                _kv._cmd("HSET", HASH_KEY, sid, json.dumps(rec, ensure_ascii=False))
        except Exception as e:
            self._json(500, {"error": f"記録に失敗: {e}"})
            return

        # 利用ログ: visitイベント（当時スコア同梱・route連結はbest-effort）
        try:
            import _eventlog
            rk, mom = _store_meta(sid)
            _eventlog.log_event({
                "ev": "visit", "visit_id": _eventlog.gen_id("v"),
                "route_id": body.get("route_id"), "owner": body.get("owner", ""),
                "id": sid, "status": body.get("status", ""), "prev_status": prev_status,
                "rank_at_visit": rk, "mom_score_at_visit": mom,
                "source": body.get("source", "store_card"),
                "note_len": len(str(body.get("memo", "") or "")),
            })
        except Exception:
            pass

        self._json(200, {"ok": True, "id": sid, "record": rec})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status, obj):
        enc = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(enc)))
        self._cors()
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(enc)

    def log_message(self, fmt, *args):
        pass
