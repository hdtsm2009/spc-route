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
import re
import sys
import unicodedata


def _hours_short(s):
    """営業時間の自由記述から代表的な時間レンジを抽出（曜日ラベル等を除去）。
    例「月・火14:00-2:00」→「14:00〜翌2:00」。最大2レンジ、全文は別途ⓘで表示。"""
    s = str(s or "")
    if not s.strip():
        return ""
    h = unicodedata.normalize("NFKC", s)
    rngs = []
    for m in re.finditer(r"(\d{1,2})(?::(\d{2}))?\s*[~〜\-―ー—]\s*(翌\s*日?)?\s*(\d{1,2})(?::(\d{2}))?", h):
        a = f"{int(m.group(1))}:{m.group(2) or '00'}"
        b = ("翌" if m.group(3) else "") + f"{int(m.group(4))}:{m.group(5) or '00'}"
        r = f"{a}〜{b}"
        if r not in rngs:
            rngs.append(r)
    if not rngs:
        return h.strip()[:14]
    out = "／".join(rngs[:2])
    return out + ("…" if len(rngs) > 2 else "")


def _monitor_short(s):
    """モニター自由記述を「最大インチ＋台数」に圧縮。例 '100インチ・計3台'。
    取れなければ プロジェクター/スクリーン有無 or '観戦設備あり'。原文は別途ⓘで全文表示。"""
    s = str(s or "").strip()
    if not s:
        return ""
    inches = [int(n) for n in re.findall(r"(\d{2,3})\s*(?:インチ|型)", s)]
    counts = [int(n) for n in re.findall(r"(\d+)\s*台", s)]
    parts = []
    if inches:
        parts.append(f"{max(inches)}インチ")
    elif "プロジェクター" in s or "スクリーン" in s or "ビジョン" in s:
        parts.append("プロジェクター/大型")
    if counts:
        parts.append(f"計{max(counts)}台" if max(counts) > 1 else "1台")
    return "・".join(parts) if parts else "観戦設備あり"

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import generate_plan as G  # filter_candidates / get_score / _visit_pitch / resolve_origin 等
import _auth

try:
    import _kv
except Exception:
    _kv = None


def _load_visits():
    """KVの訪問記録 {store_id: {status,owner,date,...}} を返す。未設定/失敗時は空。"""
    if not _kv or not _kv.is_configured():
        return {}
    try:
        flat = _kv._cmd("HGETALL", "visits") or []
        out = {}
        for i in range(0, len(flat) - 1, 2):
            try:
                out[flat[i]] = json.loads(flat[i + 1])
            except (ValueError, TypeError):
                pass
        return out
    except Exception:
        return {}


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if not _auth.check_token(self.headers):
            self._json(401, _auth.AUTH_401)
            return
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

        def _mom(r):
            try:
                return int(float(r.get("勢いスコア") or 0))
            except (ValueError, TypeError):
                return 0
        # ランク(S→AS→AF→B→C) → 勢いスコア(本命度の2次軸) → 営業スコア → 近い順
        cands.sort(key=lambda r: (G.rank_order(r), -_mom(r), -G.get_score(r), r.get("_dist_m", 1 << 30)))
        cands = cands[:limit]

        visits = _load_visits()
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
                "momentum":     _mom(r),
                "rating":       r.get("評価", ""),
                "reviews":      r.get("口コミ数", ""),
                "price":        r.get("予算", "") if str(r.get("予算", "")).startswith("¥") else "",
                "score_reason": r.get("スコア理由", ""),
                "genre":        r.get("業態ジャンル", ""),
                "pitch":        G._visit_pitch(r),
                "dist_m":       r.get("_dist_m", 0),
                "open_status":  r.get("_open_status", ""),
                "hours":        _hours_short(r.get("営業時間", "")),
                "hours_full":   r.get("営業時間", ""),
                "holiday":      r.get("定休日", ""),
                "seats":        r.get("席数", ""),
                "monitor":      _monitor_short(r.get("モニター", "")),
                "monitor_full": r.get("モニター", ""),
                "station":      r.get("最寄駅", ""),
                "plan":         r.get("スポカフェプラン", ""),
                "phone":        r.get("電話番号", ""),
                "hp":           r.get("HP", ""),
                "source":       r.get("ソース", ""),
                "chain":        r.get("chain_flag") == "チェーン疑",
                "approx":       "概算" in str(r.get("ジオコーディング精度", "")),
                "visit":        visits.get(r.get("店舗ID", ""), None),
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
