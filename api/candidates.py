"""Vercel Serverless Function: candidates （ステップ1＝店を選ぶ）
POST /api/candidates

起点（自由入力テキスト or 緯度経度）と時間帯・半径から、訪問候補の店舗を
スコア順に JSON で返す。HTMLは返さない（ルート作成は /api/generate_plan が担当）。

Request body (JSON):
  origin_text   : str   - 起点の自由入力（駅名・住所・店名）。lat/lng が無い場合に使用
  lat, lng      : float - 起点座標（origin_text より優先）
  radius_m      : int   - 抽出半径（default: configのsearch_radius_m or 1200）
  window_start  : str   - "HH:MM" (default "17:00")
  window_end    : str   - "HH:MM" (default "21:00")
  limit         : int   - 返す候補数の上限 (default 60)

Response 200 (JSON):
  {
    "origin": {"lat":..,"lng":..,"title":"..","source":"geocode|store|latlng"},
    "candidates": [ {id,name,addr,lat,lng,rank,score,score_reason,genre,
                     pitch,dist_m,open_status,phone,hp,source,chain}, ... ],
    "count": N
  }
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import generate_plan as G  # filter_candidates / get_score / _visit_pitch / resolve_origin 等


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return

        try:
            stores, cfg = G._load_data()
        except Exception as e:
            self._json(500, {"error": f"データ読み込みエラー: {e}"})
            return

        route_cfg = cfg.get("route", {})
        geocod_cfg = cfg.get("geocoding", {})

        origin = G.resolve_origin(stores, body, cfg)
        if not origin:
            self._json(400, {"error": "起点が特定できませんでした。駅名や住所を入力し直してください。"})
            return

        window_start = body.get("window_start", "17:00")
        window_end = body.get("window_end", "21:00")
        radius = int(body.get("radius_m") or route_cfg.get("search_radius_m", 1200))
        limit = int(body.get("limit", 60))

        try:
            cands = G.filter_candidates(
                stores, origin["lat"], origin["lng"], radius,
                window_start, window_end, geocod_cfg)
        except Exception as e:
            self._json(500, {"error": f"候補抽出エラー: {e}"})
            return

        cands.sort(key=lambda r: (G.rank_order(r), -G.get_score(r), r.get("_dist_m", 1 << 30)))
        cands = cands[:limit]

        out = []
        for r in cands:
            try:
                lat = float(r.get("緯度") or "")
                lng = float(r.get("経度") or "")
            except (ValueError, TypeError):
                continue
            out.append({
                "id":           r.get("店舗ID", ""),
                "name":         r.get("店名", ""),
                "addr":         r.get("住所", ""),
                "lat":          lat,
                "lng":          lng,
                "rank":         r.get("営業ランク", ""),
                "score":        G.get_score(r),
                "score_reason": r.get("スコア理由", ""),
                "genre":        r.get("業態ジャンル", ""),
                "pitch":        G._visit_pitch(r),
                "dist_m":       r.get("_dist_m", 0),
                "open_status":  r.get("_open_status", ""),
                "phone":        r.get("電話番号", ""),
                "hp":           r.get("HP", ""),
                "source":       r.get("ソース", ""),
                "chain":        r.get("chain_flag") == "チェーン疑",
                "approx":       "概算" in str(r.get("ジオコーディング精度", "")),
            })

        self._json(200, {"origin": origin, "candidates": out, "count": len(out)})

    def _json(self, status, obj):
        encoded = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        pass
