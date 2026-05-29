"""訪問ルートプラン生成（フェーズ2）。

モード:
  A. 固定アポありモード (--mode appt): アポ前後に周辺店舗を訪問
  B. 固定アポなし周遊モード (--mode free): 起点から周辺を周遊

起点指定: 住所・店名・駅名・緯度経度（マスタ検索 → 候補選択 → ジオコーディング）

使い方（対話モード）:
  python ルートプラン生成.py

引数モード（固定アポあり）:
  python ルートプラン生成.py --mode appt --appt_addr "東京都荒川区西日暮里5-9-5" \\
      --appt_start 19:00 --appt_end 20:00 --window_start 17:00 --window_end 22:00 \\
      --owner 鈴村 --max 7

引数モード（固定アポなし周遊）:
  python ルートプラン生成.py --mode free --origin "西日暮里駅" \\
      --window_start 17:00 --window_end 21:00 --owner 鈴村 --max 6 --auto
"""
import os
import csv
import json
import math
import sys
import io
import argparse
import datetime
import urllib.parse
import re
import unicodedata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
CONFIG_PATH = os.path.join(BASE, "_config", "設定.json")

with open(CONFIG_PATH, encoding="utf-8") as fp:
    CFG = json.load(fp)

ROUTE_CFG = CFG["route"]
MEMBERS = CFG["team"]["members"]


# ─── 位置計算 ─────────────────────────────────────────────────────────────────

def haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def walk_minutes(dist_m: float) -> int:
    return math.ceil(dist_m / ROUTE_CFG["walk_speed_m_per_min"])


# ─── ジオコーディング ──────────────────────────────────────────────────────────

def geocode_gsi(addr: str):
    import urllib.request
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(addr)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
        if data:
            lng, lat = data[0]["geometry"]["coordinates"]
            return float(lat), float(lng)
    except Exception:
        pass
    return None, None


# ─── マスタ読み込み ──────────────────────────────────────────────────────────

def load_master():
    for p in [
        os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv"),
        os.path.join(BASE, "_output", "統合店舗マスタ_geocoded.csv"),
        os.path.join(BASE, "_output", "統合店舗マスタ.csv"),
    ]:
        if os.path.exists(p):
            print(f"マスタ読み込み: {os.path.basename(p)}")
            return list(csv.DictReader(open(p, encoding="utf-8-sig")))
    raise FileNotFoundError("統合店舗マスタが見つかりません。フェーズ0のスクリプトを先に実行してください。")


# ─── 起点解決 ────────────────────────────────────────────────────────────────

def _normalize_query(s: str) -> str:
    """全角→半角、スペース除去、小文字化。"""
    return unicodedata.normalize("NFKC", str(s or "")).lower().replace(" ", "").replace("　", "")


def search_master_for_origin(query: str, rows: list) -> list:
    """マスタ内で店名・住所・最寄駅に部分一致する行を返す（座標あり限定）。"""
    q = _normalize_query(query)
    if not q:
        return []
    results, seen = [], set()
    for r in rows:
        matched = any(
            q in _normalize_query(r.get(col, ""))
            for col in ["店名", "住所", "最寄駅"]
        )
        if not matched:
            continue
        sid = r.get("店舗ID", "")
        if sid in seen:
            continue
        seen.add(sid)
        try:
            lat, lng = float(r.get("緯度") or ""), float(r.get("経度") or "")
        except (ValueError, TypeError):
            continue
        results.append({
            "name": r.get("店名", query),
            "addr": r.get("住所", ""),
            "lat": lat, "lng": lng,
            "type": "登録店舗",
            "store_id": r.get("店舗ID", ""),
            "source": r.get("ソース", ""),
            "spocafe": r.get("スポカフェ掲載", ""),
            "fansta": r.get("ファンスタ掲載", ""),
        })
    return results


def resolve_origin(query: str, rows: list, auto: bool = False, force_geocode: bool = False):
    """起点文字列を解決して {name, addr, lat, lng, type} を返す。失敗時は None。"""
    hits = [] if force_geocode else search_master_for_origin(query, rows)

    if len(hits) == 1:
        c = hits[0]
        print(f"  起点確定（登録店舗）: {c['name']} / {c['addr']}")
        return c

    if len(hits) > 1:
        print(f"  マスタ内に {len(hits)}件の候補が見つかりました:")
        for i, c in enumerate(hits, 1):
            spocafe = "スポカフェ掲載" if c.get("spocafe") == "○" else "未掲載"
            print(f"  [{i}] {c['name']} / {c['type']} / {c['addr'][:38]}")
            print(f"       ソース:{c.get('source','')[:15]} / {spocafe}")
        print(f"  [0] 住所/駅名としてジオコーディングする")
        print()
        if auto:
            print("  → 自動選択: [1]")
            return hits[0]
        while True:
            sel = input(f"  番号を選択（1〜{len(hits)}, 0=ジオコーディング）: ").strip()
            if sel == "0":
                break
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(hits):
                    return hits[idx]
            except ValueError:
                pass
            print("  無効な入力です。")

    # マスタになければGSIでジオコーディング
    print(f"  住所/地名としてジオコーディング: {query}")
    lat, lng = geocode_gsi(query)
    if lat:
        print(f"  → 緯度: {lat:.6f}  経度: {lng:.6f}")
        return {"name": query, "addr": query, "lat": lat, "lng": lng,
                "type": "手入力地点", "source": "", "spocafe": "", "fansta": ""}
    return None


# ─── 候補絞り込み ────────────────────────────────────────────────────────────

def check_open(hours_str: str, ws: str, we: str) -> str:
    if not hours_str or hours_str.strip() in ("", "情報なし"):
        return "要確認"
    h = unicodedata.normalize("NFKC", hours_str)
    if any(kw in h for kw in ["定休", "閉店", "営業なし"]):
        return "要確認"
    start_h = int(ws.split(":")[0])
    end_h = int(we.split(":")[0])
    hours_found = [int(hh) for hh, _ in re.findall(r"(\d{1,2})[：:時](\d{0,2})", h) if hh.isdigit()]
    if not hours_found:
        return "要確認"
    open_h, close_h = min(hours_found), max(hours_found)
    if close_h <= 5:
        close_h += 24
    if close_h <= start_h or open_h >= end_h:
        return "除外"
    return "開いている可能性高"


def filter_candidates(rows, origin_lat, origin_lng, radius_m, window_start, window_end,
                      exclude_id=None):
    geo_ok = CFG["geocoding"]["route_eligible"]
    result = []
    for r in rows:
        if exclude_id and r.get("店舗ID") == exclude_id:
            continue  # 起点店舗自身を除外
        if str(r.get("sales_status", "")).strip() == "NG":
            continue
        if str(r.get("スポカフェ掲載", "")).strip() == "○":
            continue
        try:
            lat, lng = float(r.get("緯度") or ""), float(r.get("経度") or "")
        except (ValueError, TypeError):
            continue
        if r.get("geo_quality") and r["geo_quality"] not in geo_ok:
            continue
        dist = haversine_m(origin_lat, origin_lng, lat, lng)
        if dist > radius_m:
            continue
        open_status = check_open(r.get("営業時間", ""), window_start, window_end)
        if open_status == "除外":
            continue
        r = dict(r)
        r["_dist_m"] = round(dist)
        r["_open_status"] = open_status
        result.append(r)
    return result


# ─── スコア取得 ──────────────────────────────────────────────────────────────

def get_score(r: dict) -> int:
    if r.get("営業スコア") not in (None, ""):
        try:
            return int(float(r["営業スコア"]))
        except (ValueError, TypeError):
            pass
    return {"S": 70, "A": 50, "B": 30, "C": 10}.get(r.get("営業ランク", ""), 20)


# ─── ルート組み立て ──────────────────────────────────────────────────────────

def _t2min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _min2t(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _fill_block(start_min: int, end_min: int, cur_lat: float, cur_lng: float,
                pool: list, used_ids: set, speed: float, stay: int, budget: int):
    """start_min〜end_min の空き時間をスコア降順・距離昇順で埋める。budgetまで。"""
    max_leg = ROUTE_CFG.get("max_leg_walk_min", 999)
    plan = []
    cur = start_min
    for r in pool:
        if len(plan) >= budget:
            break
        if r["店舗ID"] in used_ids:
            continue
        try:
            lat, lng = float(r["緯度"]), float(r["経度"])
        except (ValueError, TypeError):
            continue
        dist = haversine_m(cur_lat, cur_lng, lat, lng)
        travel = math.ceil(dist / speed)
        if travel > max_leg:
            continue  # 1区間の移動が上限超過 → スキップ
        if cur + travel + stay > end_min:
            continue
        cur += travel
        plan.append({
            "time": _min2t(cur),
            "depart_time": _min2t(cur + stay),
            "store": r,
            "travel_min": travel,
            "dist_m": round(dist),
        })
        used_ids.add(r["店舗ID"])
        cur_lat, cur_lng = lat, lng
        cur += stay
    return plan, cur, cur_lat, cur_lng


def build_route(candidates, appt_lat, appt_lng, appt_start, appt_end,
                window_start, window_end, max_stores):
    """固定アポありモード: アポ前後ブロックを合計 max_stores 件以内で埋める。"""
    stay = ROUTE_CFG["stay_minutes_per_store"]
    buf = ROUTE_CFG["buffer_before_appt_min"]
    speed = ROUTE_CFG["walk_speed_m_per_min"]

    pool = sorted(candidates, key=lambda r: (-get_score(r), r["_dist_m"]))
    used = set()

    pre_plan, _, pre_lat, pre_lng = _fill_block(
        _t2min(window_start), _t2min(appt_start) - buf,
        float(appt_lat), float(appt_lng), pool, used, speed, stay, max_stores)

    post_plan, _, _, _ = _fill_block(
        _t2min(appt_end), _t2min(window_end),
        float(appt_lat), float(appt_lng), pool, used, speed, stay,
        max_stores - len(pre_plan))

    appt_travel = (walk_minutes(haversine_m(pre_lat, pre_lng, float(appt_lat), float(appt_lng)))
                   if pre_plan else 0)
    return pre_plan, appt_travel, post_plan


def build_free_route(candidates, origin_lat, origin_lng, window_start, window_end,
                     max_stores, return_to_origin=False):
    """固定アポなし周遊モード: 起点から活動時間内を埋める。"""
    stay = ROUTE_CFG["stay_minutes_per_store"]
    speed = ROUTE_CFG["walk_speed_m_per_min"]

    pool = sorted(candidates, key=lambda r: (-get_score(r), r["_dist_m"]))
    used = set()
    plan, _, cur_lat, cur_lng = _fill_block(
        _t2min(window_start), _t2min(window_end),
        float(origin_lat), float(origin_lng), pool, used, speed, stay, max_stores)

    return_travel = 0
    if return_to_origin and plan:
        return_travel = walk_minutes(
            haversine_m(cur_lat, cur_lng, float(origin_lat), float(origin_lng)))
    return plan, return_travel


# ─── Google Maps リンク ──────────────────────────────────────────────────────

_GMAPS_MAX = 9  # URL内ノード上限（出発地・目的地含む）


def maps_link(addr: str, label: str = "") -> str:
    q = urllib.parse.quote(addr or label or "")
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def nav_link(from_addr: str, to_addr: str) -> str:
    return (f"https://www.google.com/maps/dir/"
            f"{urllib.parse.quote(from_addr)}/{urllib.parse.quote(to_addr)}")


def _addr_of(r: dict) -> str:
    """座標があれば "lat,lng"、なければ住所文字列を返す。"""
    try:
        return f"{float(r.get('緯度') or '')},{float(r.get('経度') or '')}"
    except (ValueError, TypeError):
        return r.get("住所", "")


def _route_urls(waypoints: list) -> list:
    """waypointリストを _GMAPS_MAX 件ずつに分割してURL一覧を返す。"""
    if len(waypoints) < 2:
        return []
    urls, step = [], _GMAPS_MAX - 1
    for i in range(0, len(waypoints) - 1, step):
        chunk = waypoints[i:i + _GMAPS_MAX]
        urls.append("https://www.google.com/maps/dir/" +
                    "/".join(urllib.parse.quote(w) for w in chunk))
    return urls


def build_gmaps_buttons(pre_plan, post_plan, appt_addr, origin_addr,
                        mode="appt", return_to_origin=False):
    """HTML に差し込む (ラベル, URL) リストを返す。"""
    buttons = []
    pre_addrs = [_addr_of(e["store"]) for e in pre_plan]
    post_addrs = [_addr_of(e["store"]) for e in post_plan]

    def add_links(label_base, wps, route_type):
        urls = _route_urls(wps)
        for i, url in enumerate(urls):
            label = label_base if len(urls) == 1 else f"{label_base}({i+1})"
            buttons.append((label, url, route_type))

    if mode == "appt":
        if pre_addrs:
            add_links("アポ前ルート", [origin_addr] + pre_addrs + [appt_addr], "before")
        if post_addrs:
            add_links("アポ後ルート", [appt_addr] + post_addrs, "after")
        if pre_addrs or post_addrs:
            all_wps = [origin_addr] + pre_addrs
            if pre_addrs:
                all_wps.append(appt_addr)
            all_wps += post_addrs
            add_links("全体ルート", all_wps, "all")
    else:
        wps = [origin_addr] + pre_addrs
        if return_to_origin:
            wps.append(origin_addr)
        add_links("周遊ルート", wps, "free")

    return buttons


# ─── HTML 出力 ───────────────────────────────────────────────────────────────

_MANUAL_HTML = """
<div id="tab-manual" style="display:none">
<div class="manual-wrap">
<h2 class="manual-h2">📖 使い方ガイド</h2>
<div class="manual-grid">

<div class="manual-card">
<h3 class="manual-h3">🚀 基本の使い方（3ステップ）</h3>
<ol class="manual-ol">
  <li><strong>Google Mapsボタンをタップ</strong><br>アポ前・アポ後・全体・周遊から状況に合ったルートをワンタップで開けます。</li>
  <li><strong>不要な店舗は「✕ 除外」</strong><br>除外するとGoogle Mapsのルートから自動で外れます。「↩ 戻す」で復元可。</li>
  <li><strong>訪問後は「📝記録」</strong><br>コマンドをコピーしてPCのPowerShellに貼り付けると記録されます。</li>
</ol>
</div>

<div class="manual-card">
<h3 class="manual-h3">📊 ランク・スコアの見方</h3>
<table class="manual-tbl">
  <tr><th>ランク</th><th>スコア</th><th>目安</th></tr>
  <tr><td><span style="color:#e74c3c;font-weight:bold">S</span></td><td>80点〜</td><td>最優先。スポーツバー・ダーツバー等</td></tr>
  <tr><td><span style="color:#e67e22;font-weight:bold">A</span></td><td>55〜79点</td><td>高優先。スポーツ観戦向け業態</td></tr>
  <tr><td><span style="color:#3498db;font-weight:bold">B</span></td><td>30〜54点</td><td>候補。ダイニングバー・バー系</td></tr>
  <tr><td><span style="color:#7f8c8d;font-weight:bold">C</span></td><td>0〜29点</td><td>低優先。カフェ等、ニーズ要確認</td></tr>
</table>
<p class="manual-note">スコア理由は「スコア理由」列（PCのみ表示）または各行の「詳細」ボタン内で確認できます。</p>
</div>

<div class="manual-card">
<h3 class="manual-h3">📋 列の説明</h3>
<table class="manual-tbl">
  <tr><th>列名</th><th>説明</th></tr>
  <tr><td>到着/出発</td><td>店舗行 → 到着時刻、移動行 → 出発時刻</td></tr>
  <tr><td>移動</td><td>前の地点からの徒歩時間・距離</td></tr>
  <tr><td>店舗</td><td>店名（タップでMaps検索）・住所</td></tr>
  <tr><td>ランク / 点数</td><td>営業優先度ランクとスコア点数</td></tr>
  <tr><td>業態</td><td>食べログ・ダーツライブから取得した業態</td></tr>
  <tr><td>電話</td><td>タップで電話発信</td></tr>
  <tr><td>📍 ナビ</td><td>前の地点から当店舗へのナビを開く</td></tr>
  <tr><td>詳細</td><td>営業時間・予算・SNS等を展開</td></tr>
  <tr><td>営業メモ</td><td>印刷用の一時メモ（保存されません）</td></tr>
  <tr><td>訪問記録</td><td>記録コマンドをコピー</td></tr>
</table>
</div>

<div class="manual-card">
<h3 class="manual-h3">🗺 Google Mapsボタン一覧</h3>
<table class="manual-tbl">
  <tr><th>ボタン名</th><th>ルート内容</th><th>モード</th></tr>
  <tr><td>アポ前ルート</td><td>起点 → アポ前店舗 → アポ先</td><td>固定アポあり</td></tr>
  <tr><td>アポ後ルート</td><td>アポ先 → アポ後店舗</td><td>固定アポあり</td></tr>
  <tr><td>全体ルート</td><td>起点 → アポ前 → アポ先 → アポ後</td><td>固定アポあり</td></tr>
  <tr><td>周遊ルート</td><td>起点から候補店舗を順番に巡回</td><td>固定アポなし</td></tr>
</table>
<p class="manual-note">店舗を除外すると「🔄 ルート更新」が自動実行されます。9店舗超の場合はルートが分割されます。</p>
</div>

<div class="manual-card">
<h3 class="manual-h3">✕ 除外機能の使い方</h3>
<ol class="manual-ol">
  <li>各行末の「✕ 除外」をクリック → 行が薄くなり、Google Mapsルートから自動除外</li>
  <li>「↩ 戻す」で1件ずつ復元、「↩ 除外をリセット」で全件一括復元</li>
</ol>
<p class="manual-note">除外状態はページを閉じると消えます。ページをリロードすると元に戻ります。</p>
<h3 class="manual-h3" style="margin-top:12px">▲ 予備候補の切り替え</h3>
<p style="font-size:12px;margin:4px 0 0">「── 予備候補 ──」行の右端「▲ 隠す」で予備を一括非表示にできます。</p>
<p class="manual-note">「▲ 隠す」で予備候補を非表示にすると、Google Mapsルートからも自動的に外れます。再表示すると元のルートに戻ります。</p>
</div>

<div class="manual-card">
<h3 class="manual-h3">📝 訪問記録の残し方</h3>
<ol class="manual-ol">
  <li>「📝記録」ボタンをクリック → Pythonコマンドがクリップボードにコピー</li>
  <li>PCのPowerShellを開き、コピーしたコマンドを貼り付けて実行</li>
  <li>対話形式で訪問日・接触方法・結果・次回アクションを入力</li>
  <li><code>統合店舗マスタ_v2.csv</code> と <code>_feedback/visit_results.csv</code> に自動記録</li>
</ol>
<p class="manual-note">スマートフォンからの記録はβ版では未対応です。本運用時はGoogleフォームへの移行を予定しています。</p>
</div>

<div class="manual-card">
<h3 class="manual-h3">ℹ️ スコアの加点・減点ルール</h3>
<table class="manual-tbl">
  <tr><th>条件</th><th style="text-align:right">点数</th></tr>
  <tr><td>スポカフェ未掲載</td><td class="score-plus">+25</td></tr>
  <tr><td>ファンスタ未掲載</td><td class="score-plus">+15</td></tr>
  <tr><td>食べログ由来</td><td class="score-plus">+15</td></tr>
  <tr><td>複数ソース一致</td><td class="score-plus">+10</td></tr>
  <tr><td>ダーツライブ由来</td><td class="score-plus">+10</td></tr>
  <tr><td>評価・口コミ高</td><td class="score-plus">+10</td></tr>
  <tr><td>スポーツバー / PV業態</td><td class="score-plus">+20</td></tr>
  <tr><td>ダーツ / ビリヤード業態</td><td class="score-plus">+15</td></tr>
  <tr><td>ダイニングバー / パブ業態</td><td class="score-plus">+10</td></tr>
  <tr><td>住所精度A（番地あり）</td><td class="score-plus">+5</td></tr>
  <tr><td>スポカフェ掲載済</td><td class="score-minus">-100</td></tr>
  <tr><td>訪問NG済</td><td class="score-minus">-100</td></tr>
  <tr><td>スペースマーケット単一ソース</td><td class="score-minus">-15</td></tr>
  <tr><td>スペース貸し業態</td><td class="score-minus">-25</td></tr>
  <tr><td>住所精度C/NG</td><td class="score-minus">-20</td></tr>
  <tr><td>フリーダイヤル検出（FC?）</td><td class="score-minus">-10</td></tr>
</table>
</div>

<div class="manual-card">
<h3 class="manual-h3">🏷 バッジの見方</h3>
<table class="manual-tbl">
  <tr><th>バッジ</th><th>意味</th></tr>
  <tr><td><span style="background:#eaf6fb;color:#1a6a8a;font-size:11px;padding:1px 6px;border-radius:8px;border:1px solid #aad4e8">両サービス未掲載</span></td><td>スポカフェ・ファンスタいずれにも未掲載。掲載提案の最優先ターゲット</td></tr>
  <tr><td><span style="background:#eaf6fb;color:#1a6a8a;font-size:11px;padding:1px 6px;border-radius:8px;border:1px solid #aad4e8">スポーツバー◎</span></td><td>スポーツバー・PV実施業態。訪問理由がそのまま営業トークになる</td></tr>
  <tr><td><span style="background:#fff3cd;color:#856404;font-size:11px;padding:1px 5px;border-radius:3px;border:1px solid #ffc107;font-weight:bold">FC?</span></td><td>フリーダイヤル（0120/0800/0570）等から<strong>チェーン・本部系の可能性がある</strong>と推定された店舗。確定判定ではありません。個店向け飛び込みではなく、<strong>本部確認や担当者確認が必要な場合</strong>があります。</td></tr>
</table>
<p class="manual-note">FC? はあくまで補助表示です。フリーダイヤルを持つ個店もあるため、訪問前に確認することをお勧めします。</p>
</div>

</div>
</div>
</div>
"""

RANK_COLOR = {"S": "#e74c3c", "A": "#e67e22", "B": "#3498db", "C": "#7f8c8d", "除外": "#bdc3c7"}
OPEN_ICON = {"開いている可能性高": "🟢", "要確認": "🟡", "除外": "🔴"}


def _phone_cell(phone: str) -> str:
    """電話番号セル。数字を含む有効な番号のみ tel: リンク化。
    「不明」「情報お待ち」等の無効値はテキスト（リンクなし）で表示。"""
    p = (phone or "").strip()
    if not p:
        return ""
    digits = re.sub(r"[^\d]", "", p)
    if len(digits) < 9 or any(kw in p for kw in ("不明", "情報", "お待ち", "確認", "なし")):
        return f"<small>{p}</small>"
    tel = re.sub(r"[^\d+]", "", p)
    return f'<a href="tel:{tel}">{p}</a>'


def _detail_html(r: dict) -> str:
    hp = r.get("HP", "")
    hp_cell = f'<a href="{hp}" target="_blank">{hp[:50]}</a>' if hp and hp.startswith("http") else hp
    sales_fields = [
        ("評価", r.get("評価")), ("口コミ数", r.get("口コミ数")),
        ("営業時間", r.get("営業時間")), ("予算", r.get("予算")),
        ("最寄駅", r.get("最寄駅")), ("HP", hp_cell),
        ("SNS", r.get("SNS")),
    ]
    mgmt_fields = [
        ("スポカフェ掲載", r.get("スポカフェ掲載") or "未掲載"),
        ("ファンスタ掲載", r.get("ファンスタ掲載") or "未掲載"),
        ("ソース", r.get("ソース")),
        ("geo_quality", r.get("geo_quality")),
        ("ジオコーディング精度", r.get("ジオコーディング精度")),
        ("店舗ID", r.get("店舗ID")),
        ("名寄せ_電話キー", r.get("名寄せ_電話キー")),
        ("名寄せ_店名キー", r.get("名寄せ_店名キー")),
    ]
    def tbl(fields):
        return "".join(
            f"<tr><td class='dl'>{lbl}</td><td>{val}</td></tr>"
            for lbl, val in fields if val
        )
    sales_tbl = tbl(sales_fields)
    mgmt_tbl = tbl(mgmt_fields)
    mgmt_block = (
        f'<details class="detail-mgmt">'
        f'<summary style="font-size:10px;color:#aaa;cursor:pointer;margin-top:4px">▸ 管理情報</summary>'
        f'<table class="detail-tbl">{mgmt_tbl}</table>'
        f'</details>'
    ) if mgmt_tbl else ""
    return (f'<details class="store-detail"><summary>詳細</summary>'
            f'<div class="detail-body">'
            f'<table class="detail-tbl">{sales_tbl}</table>'
            f'{mgmt_block}'
            f'</div></details>')


def _visit_pitch(r: dict) -> str:
    """「なぜ今この店か」の一言サマリーを返す（20文字以内）。"""
    unlisted_spo = r.get("スポカフェ掲載") != "○"
    unlisted_fan = r.get("ファンスタ掲載") != "○"
    sources = str(r.get("ソース", ""))
    genre = str(r.get("業態ジャンル", ""))
    rating = 0.0
    try:
        rating = float(r.get("評価") or 0)
    except (ValueError, TypeError):
        pass
    rank = r.get("営業ランク", "")

    if unlisted_spo and unlisted_fan and rank in ("S", "A"):
        return "両サービス未掲載"
    if "スポーツバー" in genre or "パブリックビューイング" in genre:
        return "スポーツバー◎"
    if "ダーツ" in genre:
        return "ダーツバー◎"
    if unlisted_spo and unlisted_fan:
        return "2サービス未掲載"
    if "ダーツライブ" in sources:
        return "ダーツライブ掲載"
    if unlisted_spo:
        return "スポカフェ未掲載"
    if unlisted_fan:
        return "ファンスタ未掲載"
    if rating >= 3.8:
        return f"評価{rating}★"
    if rank == "S":
        return "Sランク店"
    return ""


def _store_row_html(entry: dict, prev_addr: str, owner: str, section: str = "free") -> str:
    r = entry["store"]
    rank = r.get("営業ランク", "")
    rc = RANK_COLOR.get(rank, "#999")
    oi = OPEN_ICON.get(r.get("_open_status", ""), "")
    addr = r.get("住所", "")
    ml = maps_link(addr, r.get("店名", ""))
    nl = nav_link(prev_addr, addr)
    score = r.get("営業スコア", "")
    sr = r.get("スコア理由", "")
    phone = r.get("電話番号", "")
    genre = r.get("業態ジャンル", "")
    store_id = r.get("店舗ID", "")
    rec_cmd = f'python _scripts/訪問記録.py --store_id {store_id} --owner {owner}'
    addr_esc = addr.replace('"', '&quot;')
    priority = "backup" if "-backup" in section else "main"
    sec = section.replace("-backup", "")
    try:
        latlng = f"{float(r.get('緯度') or '')},{float(r.get('経度') or '')}"
    except (ValueError, TypeError):
        latlng = ""
    pitch = _visit_pitch(r)
    pitch_html = f'<br><span class="pitch-chip">{pitch}</span>' if pitch else ""
    chain_html = '<span class="chain-badge">FC?</span>' if r.get("chain_flag") == "チェーン疑" else ""
    return f"""
    <tr class="store-row" data-addr="{addr_esc}" data-latlng="{latlng}" data-section="{sec}" data-priority="{priority}">
      <td>{entry['time']}</td>
      <td>🚶 徒歩{entry['travel_min']}分<br><small>{entry['dist_m']}m</small></td>
      <td><a href="{ml}" target="_blank"><strong>{r['店名']}</strong></a>{chain_html}
          <br><small class="addr">{addr}</small>
          <br><small class="store-id">ID: {store_id}</small>{pitch_html}</td>
      <td><span style="color:{rc};font-weight:bold">{rank}</span> {oi}
          <br><small title="{sr.replace('"', "'")}">{score}点</small></td>
      <td class="reason-cell"><small>{sr[:60]}{'…' if len(sr) > 60 else ''}</small></td>
      <td><small>{genre[:18]}</small></td>
      <td><small>{r.get('ソース', '')}</small></td>
      <td>{_phone_cell(phone)}</td>
      <td><a href="{nl}" target="_blank">📍</a></td>
      <td>{_detail_html(r)}</td>
      <td class="memo-cell" contenteditable="true"></td>
      <td><button class="rec-btn" onclick="copyCmd(this)" data-cmd="{rec_cmd}">📝記録</button>
          <br><button class="exclude-btn" onclick="toggleExclude(this)">✕ 除外</button></td>
    </tr>"""


_CSS = """
  body{font-family:'Hiragino Sans','Meiryo',sans-serif;font-size:13px;margin:20px;color:#333}
  h1{font-size:18px;margin-bottom:4px}
  .beta-banner{background:#fff8e1;border-left:4px solid #f39c12;padding:8px 14px;margin-bottom:12px;
               border-radius:0 4px 4px 0;font-size:12px;color:#7d5a00}
  .beta-banner strong{font-size:13px}
  .meta{color:#666;font-size:12px;margin-bottom:8px}
  .gmaps-bar{margin-bottom:14px;display:flex;gap:10px;flex-wrap:wrap}
  .gmaps-btn{display:inline-block;padding:6px 14px;background:#4285f4;color:#fff!important;
             border-radius:4px;font-size:12px;text-decoration:none}
  .gmaps-btn:hover{background:#2a6dd9}
  table{border-collapse:collapse;width:100%}
  th{background:#2c3e50;color:#fff;padding:6px 8px;text-align:left}
  td{padding:5px 8px;border-bottom:1px solid #e0e0e0;vertical-align:top}
  tr:hover{background:#f9f9f9}
  .section-header td{background:#ecf0f1;font-weight:bold;color:#555;padding:4px 8px}
  .priority-sep td{background:#fef9e7;color:#7d5a00;border-top:2px dashed #f39c12}
  .appt-row td{background:#fff3cd;font-weight:bold}
  .appt-move td{background:#f0f8ff;color:#555;font-style:italic}
  .memo-cell{min-width:100px;background:#fffef0}
  .reason-cell{max-width:160px;color:#555}
  .store-id{color:#aaa;font-family:monospace}
  .addr{color:#666}
  .rec-btn{font-size:11px;padding:2px 6px;cursor:pointer;background:#eaf4fb;
           border:1px solid #aad4e8;border-radius:3px}
  .rec-btn:hover{background:#cce8f5}
  .copy-toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
              background:#333;color:#fff;padding:8px 18px;border-radius:6px;font-size:13px;display:none}
  a{color:#2980b9;text-decoration:none}
  a:hover{text-decoration:underline}
  .summary{background:#f4f4f4;padding:12px;border-radius:6px;margin-bottom:16px;
           display:flex;gap:24px;flex-wrap:wrap}
  .summary span{font-size:13px}
  details.store-detail summary{cursor:pointer;font-size:11px;color:#2980b9;padding:2px 4px}
  details.store-detail summary:hover{text-decoration:underline}
  .detail-body{padding:6px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:3px;
               margin-top:4px;min-width:200px}
  table.detail-tbl{width:100%;font-size:11px}
  table.detail-tbl td.dl{color:#888;padding-right:8px;white-space:nowrap}
  table.detail-tbl td{padding:2px 4px;border:none}
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  tr.store-row.row-excluded{opacity:.3}
  tr.store-row.row-excluded td{text-decoration:line-through;color:#aaa}
  .exclude-btn{font-size:10px;padding:2px 5px;cursor:pointer;background:#fef0f0;
               border:1px solid #f5c6cb;border-radius:3px;color:#c0392b;margin-top:3px}
  .exclude-btn:hover{background:#fdd}
  .exclude-badge{display:inline-block;background:#e74c3c;color:#fff;font-size:11px;
                 padding:2px 8px;border-radius:10px;margin-left:10px;vertical-align:middle}
  .backup-toggle-btn{font-size:11px;padding:2px 8px;cursor:pointer;background:#fff8e1;
                     border:1px solid #f39c12;border-radius:3px;margin-left:12px;color:#7d5a00}
  .route-ops{margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap}
  .route-ops button{padding:5px 12px;cursor:pointer;background:#f0f8ff;
                    border:1px solid #aad4e8;border-radius:4px;font-size:12px}
  .route-ops button:hover{background:#cce8f5}
  .tab-bar{display:flex;border-bottom:3px solid #2c3e50;margin-bottom:0}
  .tab-btn{padding:8px 20px;border:1px solid #ccc;border-bottom:none;background:#ecf0f1;
           cursor:pointer;font-size:13px;border-radius:6px 6px 0 0;margin-right:4px;
           color:#555;transition:background .15s}
  .tab-btn.active{background:#2c3e50;color:#fff;font-weight:bold;border-color:#2c3e50}
  .tab-btn:hover:not(.active){background:#d5d8dc}
  #tab-plan{padding-top:16px}
  .manual-wrap{max-width:960px;padding:16px 0}
  .manual-h2{font-size:17px;margin-bottom:16px;color:#2c3e50}
  .manual-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:14px}
  .manual-card{background:#fff;border:1px solid #dde;border-radius:6px;padding:14px 16px;
               box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .manual-h3{font-size:13px;color:#2c3e50;border-left:4px solid #2c3e50;
             padding-left:8px;margin:0 0 10px}
  .manual-tbl{border-collapse:collapse;width:100%;font-size:11px;margin-bottom:6px}
  .manual-tbl th{background:#2c3e50;color:#fff;padding:4px 7px;text-align:left}
  .manual-tbl td{padding:4px 7px;border-bottom:1px solid #eee}
  .manual-tbl tr:hover td{background:#f8f9fa}
  .manual-ol{padding-left:18px;font-size:12px;margin:0}
  .manual-ol li{margin-bottom:6px;line-height:1.5}
  .manual-note{font-size:11px;color:#888;background:#f8f9fa;padding:5px 8px;
               border-radius:3px;border-left:3px solid #bbb;margin:6px 0 0}
  .manual-note code{background:#eee;padding:1px 4px;border-radius:2px;font-size:10px}
  .pitch-chip{display:inline-block;background:#eaf6fb;color:#1a6a8a;font-size:10px;
              padding:1px 6px;border-radius:8px;border:1px solid #aad4e8;margin-top:2px}
  .chain-badge{display:inline-block;background:#fff3cd;color:#856404;font-size:10px;
               padding:1px 5px;border-radius:3px;border:1px solid #ffc107;margin-left:4px;
               vertical-align:middle;font-weight:bold}
  .score-plus{color:#27ae60;font-weight:bold;text-align:right}
  .score-minus{color:#e74c3c;font-weight:bold;text-align:right}
  @media(max-width:768px){.manual-grid{grid-template-columns:1fr}}
  @media(max-width:768px){
    body{margin:8px;font-size:12px}
    h1{font-size:15px}
    .gmaps-bar{flex-direction:column;gap:6px}
    .gmaps-btn{padding:10px 16px;font-size:13px;text-align:center;flex:1 1 100%}
    .summary{flex-direction:column;gap:6px}
    .store-id{display:none}
    th:nth-child(5),td:nth-child(5),
    th:nth-child(7),td:nth-child(7),
    th:nth-child(11),td:nth-child(11){display:none!important}
    th:nth-child(3),td:nth-child(3){position:sticky;left:0;z-index:1;
      box-shadow:2px 0 3px rgba(0,0,0,.08);min-width:110px;max-width:150px}
    td:nth-child(3){background:#fff}
    tr:hover td:nth-child(3){background:#f9f9f9}
    .appt-row td:nth-child(3){background:#fff3cd}
    .appt-move td:nth-child(3){background:#f0f8ff}
    .section-header td:nth-child(3){background:#ecf0f1}
    th:nth-child(3){background:#2c3e50}
    .rec-btn,.exclude-btn{padding:5px 8px;font-size:11px}
  }
  @media print{
    .memo-cell{border:1px solid #ccc}
    .rec-btn,.exclude-btn,.copy-toast,.beta-banner,.gmaps-bar{display:none!important}
    details.store-detail{display:none}
    tr.store-row.row-excluded{display:none}
  }
"""

_JS = """
function switchTab(tab){
  ['plan','manual'].forEach(t=>{
    const el=document.getElementById('tab-'+t);
    if(el) el.style.display=t===tab?'':'none';
  });
  document.querySelectorAll('.tab-btn').forEach(b=>{
    b.classList.toggle('active',b.dataset.tab===tab);
  });
}
function copyCmd(btn){
  const cmd=btn.getAttribute('data-cmd');
  navigator.clipboard.writeText(cmd).then(()=>{
    const t=document.getElementById('copy-toast');
    t.textContent='コマンドをコピーしました: '+cmd;
    t.style.display='block';
    setTimeout(()=>{t.style.display='none'},3000);
  });
}
function toggleExclude(btn){
  const row=btn.closest('tr.store-row');
  const excluded=row.classList.toggle('row-excluded');
  btn.textContent=excluded?'↩ 戻す':'✕ 除外';
  updateExcludeCount();
  rebuildRoutes();
}
function updateExcludeCount(){
  const n=document.querySelectorAll('tr.store-row.row-excluded').length;
  const badge=document.getElementById('exclude-badge');
  if(badge) badge.textContent=n>0?'除外中: '+n+'件':'';
}
function resetExclusions(){
  document.querySelectorAll('tr.store-row.row-excluded').forEach(row=>{
    row.classList.remove('row-excluded');
    const b=row.querySelector('.exclude-btn');
    if(b) b.textContent='✕ 除外';
  });
  updateExcludeCount();
  rebuildRoutes();
}
function toggleBackup(){
  const btn=document.getElementById('backup-toggle');
  if(!btn) return;
  const hide=btn.dataset.state==='visible';
  document.querySelectorAll('tr.store-row[data-priority="backup"]').forEach(row=>{
    row.style.display=hide?'none':'';
  });
  btn.dataset.state=hide?'hidden':'visible';
  btn.textContent=hide?'▼ 表示':'▲ 隠す';
  rebuildRoutes();
}
// ── Google Maps URL 再生成 ──────────────────────────────────────────
function _wp(row){return row.dataset.latlng||row.dataset.addr||'';}
function _gurl(wps){
  const c=wps.filter(w=>w);
  if(c.length<2) return '';
  return 'https://www.google.com/maps/dir/'+c.map(encodeURIComponent).join('/');
}
function _splitUrls(wps){
  const c=wps.filter(w=>w);
  if(c.length<2) return [];
  const max=9,step=max-1,urls=[];
  for(let i=0;i<c.length-1;i+=step){
    const chunk=c.slice(i,i+max);
    if(chunk.length>=2) urls.push(_gurl(chunk));
  }
  return urls;
}
function _getAddrs(section){
  return Array.from(
    document.querySelectorAll('tr.store-row:not(.row-excluded)[data-section="'+section+'"]')
  ).filter(row=>row.style.display!=='none').map(_wp);
}
function _updateBtn(routeType,urls){
  const btns=document.querySelectorAll('.gmaps-btn[data-route-type="'+routeType+'"]');
  if(!btns.length) return;
  btns.forEach((b,i)=>{
    if(i===0&&urls.length>0){
      b.href=urls[0];
      b.style.opacity='1';
      b.style.pointerEvents='';
      if(b.dataset.origText) b.textContent=b.dataset.origText;
    } else if(i===0){
      if(!b.dataset.origText) b.dataset.origText=b.textContent;
      b.removeAttribute('href');
      b.style.opacity='0.4';
      b.style.pointerEvents='none';
      b.textContent='🗺 ルートなし';
    } else {
      b.style.display=urls.length>1?'':'none';
    }
  });
}
function rebuildRoutes(){
  const meta=document.getElementById('route-meta');
  if(!meta) return;
  const mode=meta.dataset.mode;
  const origin=meta.dataset.origin;
  const apptAddr=meta.dataset.apptAddr;
  const rto=meta.dataset.returnToOrigin==='true';
  if(mode==='appt'){
    const before=_getAddrs('before');
    const after=_getAddrs('after');
    _updateBtn('before',_splitUrls([origin,...before,apptAddr]));
    _updateBtn('after',_splitUrls([apptAddr,...after]));
    _updateBtn('all',_splitUrls([origin,...before,apptAddr,...after]));
  } else {
    const free=_getAddrs('free');
    const wps=rto?[origin,...free,origin]:[origin,...free];
    _updateBtn('free',_splitUrls(wps));
  }
}
"""


def render_html(pre_plan, appt_travel, post_plan, appt_info, owner, generated_at,
                mode="appt", origin=None, gmaps_buttons=None, return_travel=0):
    priority_n = appt_info.get("priority_n", 6)
    gmaps_buttons = gmaps_buttons or []
    origin = origin or {}

    total = len(pre_plan) + (len(post_plan) if mode == "appt" else 0)
    show_sep = total > priority_n
    store_count = 0
    sep_shown = False
    rows_html = ""
    prev = appt_info.get("addr", origin.get("addr", ""))

    def maybe_sep():
        nonlocal sep_shown
        if show_sep and store_count == priority_n and not sep_shown:
            sep_shown = True
            return ('<tr class="section-header priority-sep">'
                    '<td colspan="12">── 予備候補（時間が余れば） ──'
                    '<button id="backup-toggle" class="backup-toggle-btn" '
                    'onclick="toggleBackup()" data-state="visible">▲ 隠す</button>'
                    '</td></tr>')
        return ""

    if mode == "free":
        if pre_plan:
            rows_html += '<tr class="section-header"><td colspan="12">── 訪問順 ──</td></tr>'
        else:
            rows_html += (
                '<tr><td colspan="12" style="text-align:center;padding:28px 12px;color:#888">'
                '😢 指定された条件に一致する候補店舗が見つかりませんでした。'
                '<br><small>活動時間や担当エリアを変えて再生成してください。</small>'
                '</td></tr>')
        for idx, e in enumerate(pre_plan):
            sec = "free-backup" if idx >= priority_n else "free"
            rows_html += maybe_sep()
            rows_html += _store_row_html(e, prev, owner, section=sec)
            prev = e["store"].get("住所", prev)
            store_count += 1
        if return_travel > 0 and pre_plan:
            rows_html += f"""
            <tr class="appt-move">
              <td>{pre_plan[-1]['depart_time']}</td>
              <td>🚶 徒歩{return_travel}分</td>
              <td colspan="10">→ 起点へ戻る（{origin.get('name', '')}）</td>
            </tr>"""
    else:
        if pre_plan:
            hdr = f'── アポ前（推奨 {priority_n}件） ──' if show_sep else '── アポ前 ──'
            rows_html += f'<tr class="section-header"><td colspan="12">{hdr}</td></tr>'
            for idx, e in enumerate(pre_plan):
                sec = "before-backup" if idx >= priority_n else "before"
                rows_html += maybe_sep()
                rows_html += _store_row_html(e, prev, owner, section=sec)
                prev = e["store"].get("住所", prev)
                store_count += 1

        move_time = pre_plan[-1]['depart_time'] if pre_plan else appt_info.get("window_start", "")
        rows_html += f"""
        <tr class="appt-move">
          <td>{move_time}</td>
          <td>🚶 徒歩{appt_travel}分</td>
          <td colspan="10">→ アポ先へ移動</td>
        </tr>"""

        ml_appt = maps_link(appt_info.get("addr", ""))
        rows_html += f"""
        <tr class="appt-row">
          <td>{appt_info.get('start', '')} - {appt_info.get('end', '')}</td>
          <td>📌 アポイント</td>
          <td><a href="{ml_appt}" target="_blank"><strong>{appt_info.get('name', '')}</strong></a>
              <br><small>{appt_info.get('addr', '')}</small></td>
          <td colspan="9">（固定）</td>
        </tr>"""
        prev = appt_info.get("addr", "")

        if post_plan:
            rows_html += '<tr class="section-header"><td colspan="12">── アポ後 ──</td></tr>'
            for e in post_plan:
                rows_html += maybe_sep()
                rows_html += _store_row_html(e, prev, owner, section="after")
                prev = e["store"].get("住所", prev)
                store_count += 1

    # Google Mapsボタン
    gmaps_html = ""
    if gmaps_buttons:
        btns = " ".join(
            f'<a class="gmaps-btn" href="{url}" target="_blank" data-route-type="{rt}">🗺 {lbl}</a>'
            for lbl, url, rt in gmaps_buttons
        )
        gmaps_html = f'<div class="gmaps-bar">{btns}</div>'

    mode_label = "固定アポあり" if mode == "appt" else "固定アポなし周遊"
    priority_label = (f"推奨{min(priority_n, total)}件 + 予備{max(total - priority_n, 0)}件"
                      if show_sep else f"{total}件")
    appt_span = (f'<span>📌 アポ: {appt_info.get("start", "")}〜{appt_info.get("end", "")}</span>'
                 if mode == "appt" else "")
    # Fix3: 固定アポありではアポ名をタイトル/h1に使う
    if mode == "appt":
        display_name = appt_info.get("name", "")
    else:
        display_name = origin.get("name", appt_info.get("name", ""))
    origin_addr_disp = origin.get("addr", appt_info.get("addr", ""))
    # Fix4: 終了地点ラベル（周遊モードのみ）
    if mode == "free":
        endpoint_label = "起点に戻る" if appt_info.get("return_to_origin") else "最後の訪問店舗で終了"
        endpoint_span = f'<span>🏁 終了地点: {endpoint_label}</span>'
    else:
        endpoint_span = ""

    origin_meta = origin.get("addr", "").replace('"', '&quot;')
    appt_meta = appt_info.get("addr", "").replace('"', '&quot;')
    rto_meta = str(appt_info.get("return_to_origin", False)).lower()
    route_meta_html = (
        f'<div id="route-meta" data-mode="{mode}" '
        f'data-origin="{origin_meta}" data-appt-addr="{appt_meta}" '
        f'data-return-to-origin="{rto_meta}" style="display:none"></div>'
    )
    route_ops_html = (
        '<div class="route-ops">'
        '<button onclick="rebuildRoutes()">🔄 ルート更新</button>'
        '<button onclick="resetExclusions()">↩ 除外をリセット</button>'
        '</div>'
    ) if gmaps_buttons else ""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>訪問プラン – {display_name}</title>
<style>{_CSS}</style>
<script>{_JS}</script>
</head>
<body>
<div class="beta-banner">
  <strong>β版：営業担当レビュー用</strong><br>
  候補店舗・訪問順・表示項目の妥当性確認が目的です。訪問記録の本格運用は未対応です。<br>
  <strong>⚠ 社内確認用 — URL・パスワードの社外共有は禁止です。</strong>
</div>
<div class="tab-bar">
  <button class="tab-btn active" data-tab="plan" onclick="switchTab('plan')">📍 訪問プラン</button>
  <button class="tab-btn" data-tab="manual" onclick="switchTab('manual')">📖 使い方</button>
</div>
<div id="tab-plan">
<h1>📍 訪問プラン – {display_name}<span id="exclude-badge" class="exclude-badge"></span></h1>
<div class="meta">モード: {mode_label} ／ 起点: {origin_addr_disp} ／ 担当: {owner} ／ 生成: {generated_at}</div>
{gmaps_html}
{route_ops_html}
{route_meta_html}
<div class="summary">
  <span>⏱ 活動時間: {appt_info['window_start']}〜{appt_info['window_end']}</span>
  {appt_span}
  {endpoint_span}
  <span>📌 訪問予定: {priority_label}</span>
  <span>🔍 候補抽出半径（起点から）: {appt_info['radius']}m</span>
</div>
<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th>到着/出発</th><th>移動</th><th>店舗</th><th>ランク</th><th>スコア理由</th>
      <th>業態</th><th>ソース</th><th>電話</th><th>ナビ</th><th>詳細</th><th>営業メモ</th><th>訪問記録</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</div>
<div class="copy-toast" id="copy-toast"></div>
<p style="color:#888;font-size:11px;margin-top:12px">
※ 到着/出発欄：店舗行は到着時刻、移動行は出発時刻。各店舗の滞在目安は{ROUTE_CFG['stay_minutes_per_store']}分。<br>
※ 営業メモ欄は印刷用メモです（閉じると消えます）。訪問結果の記録は 📝記録ボタン → PowerShellに貼り付けて実行してください。
</p>
</div>
{_MANUAL_HTML}
</body>
</html>"""


# ─── メイン ─────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="訪問ルートプラン生成（フェーズ2）")
    p.add_argument("--mode", choices=["appt", "free"], default="")
    # 固定アポありモード
    p.add_argument("--appt_name", default="")
    p.add_argument("--appt_addr", default="")
    p.add_argument("--appt_start", default="")
    p.add_argument("--appt_end", default="")
    # 周遊モード
    p.add_argument("--origin", default="", help="周遊モードの起点（住所・店名・駅名）")
    p.add_argument("--return_to_origin", action="store_true")
    # 共通
    p.add_argument("--window_start", default="")
    p.add_argument("--window_end", default="")
    p.add_argument("--owner", default="")
    p.add_argument("--max", type=int, default=ROUTE_CFG["max_candidates"])
    p.add_argument("--priority_n", type=int, default=ROUTE_CFG["priority_n"])
    p.add_argument("--radius", type=int, default=ROUTE_CFG["search_radius_m"])
    p.add_argument("--auto", action="store_true", help="起点候補を自動選択（非対話）")
    p.add_argument("--force_geocode", action="store_true",
                   help="マスタ検索をスキップしてジオコーディング強制（駅名指定時など）")
    p.add_argument("--include_origin_store", action="store_true",
                   help="店名起点の場合でも起点店舗自身を訪問候補に含める")
    return p.parse_args()


def _ask(prompt, default="", choices=None):
    if choices:
        hint = f"[{'/'.join(choices)}]"
    else:
        hint = f"[{default}]" if default else ""
    val = input(f"{prompt} {hint}: ").strip()
    return val if val else default


def main():
    args = parse_args()

    print("=" * 60)
    print("  訪問プラン生成ツール（フェーズ2）")
    print("=" * 60)

    rows = load_master()

    # モード自動判定（後方互換: --appt_addr があれば appt, --origin があれば free）
    mode = args.mode
    if not mode:
        if args.appt_addr:
            mode = "appt"
        elif args.origin:
            mode = "free"
        else:
            print("\n  モードを選択してください:")
            print("  [1] 固定アポあり（アポ前後に周辺店舗を回る）")
            print("  [2] 固定アポなし周遊（起点から出発して周辺を回る）")
            mode = "free" if _ask("  番号", "1") == "2" else "appt"

    now = datetime.datetime.now().strftime("%Y%m%d_%H%M")

    # ──────────── 固定アポありモード ────────────
    if mode == "appt":
        batch = bool(args.appt_addr)
        appt_name = args.appt_name or ("アポ先" if batch else _ask("アポ先名称", "アポ先"))
        origin_query = args.appt_addr or _ask("アポ先住所または地点名（例: 西日暮里駅）")
        appt_start = args.appt_start or _ask("アポ開始時刻（例: 19:00）")
        appt_end = args.appt_end or _ask("アポ終了時刻（例: 20:00）")
        window_start = args.window_start or _ask("活動開始時刻（例: 17:00）")
        window_end = args.window_end or _ask("活動終了時刻（例: 22:00）")
        owner = args.owner or _ask("担当者名", choices=MEMBERS)
        max_stores = args.max
        priority_n = args.priority_n
        radius = args.radius

        print(f"\n起点を解決中: {origin_query}")
        origin = resolve_origin(origin_query, rows, auto=(args.auto or batch),
                               force_geocode=args.force_geocode)
        if not origin:
            print("❌ 起点の座標取得に失敗しました。住所を確認してください。")
            sys.exit(1)

        exclude_id = None if args.include_origin_store else origin.get("store_id")
        print(f"\n候補絞り込み中（半径{radius}m, {window_start}〜{window_end}）...")
        cands = filter_candidates(rows, origin["lat"], origin["lng"], radius, window_start, window_end,
                                  exclude_id=exclude_id)
        print(f"  → {len(cands)}件")
        if not cands:
            print("⚠ 候補が見つかりませんでした。半径や時間帯を広げてみてください。")
            sys.exit(0)

        pre_plan, appt_travel, post_plan = build_route(
            cands, origin["lat"], origin["lng"],
            appt_start, appt_end, window_start, window_end, max_stores)
        total = len(pre_plan) + len(post_plan)
        print(f"  → プラン確定: {total}件（前{len(pre_plan)}件 + 後{len(post_plan)}件）")

        gmaps_buttons = build_gmaps_buttons(
            pre_plan, post_plan, origin["addr"], origin["addr"], mode="appt")

        appt_info = {
            "name": appt_name, "addr": origin["addr"],
            "start": appt_start, "end": appt_end,
            "window_start": window_start, "window_end": window_end,
            "radius": radius, "priority_n": priority_n,
        }
        html = render_html(pre_plan, appt_travel, post_plan, appt_info, owner, now,
                           mode="appt", origin=origin, gmaps_buttons=gmaps_buttons)
        filename = f"訪問プラン_{appt_name[:20]}_{now}.html"

    # ──────────── 固定アポなし周遊モード ────────────
    else:
        batch = bool(args.origin)
        origin_query = args.origin or _ask("起点（住所・店名・駅名）")
        window_start = args.window_start or _ask("活動開始時刻（例: 17:00）")
        window_end = args.window_end or _ask("活動終了時刻（例: 21:00）")
        owner = args.owner or _ask("担当者名", choices=MEMBERS)
        max_stores = args.max
        priority_n = args.priority_n
        radius = args.radius
        return_to_origin = args.return_to_origin

        print(f"\n起点を解決中: {origin_query}")
        origin = resolve_origin(origin_query, rows, auto=(args.auto or batch),
                               force_geocode=args.force_geocode)
        if not origin:
            print("❌ 起点の座標取得に失敗しました。")
            sys.exit(1)

        exclude_id = None if args.include_origin_store else origin.get("store_id")
        print(f"\n候補絞り込み中（半径{radius}m, {window_start}〜{window_end}）...")
        cands = filter_candidates(rows, origin["lat"], origin["lng"], radius, window_start, window_end,
                                  exclude_id=exclude_id)
        print(f"  → {len(cands)}件")
        if not cands:
            print("⚠ 候補が見つかりませんでした。")
            sys.exit(0)

        plan, return_travel = build_free_route(
            cands, origin["lat"], origin["lng"],
            window_start, window_end, max_stores, return_to_origin)
        print(f"  → プラン確定: {len(plan)}件")

        gmaps_buttons = build_gmaps_buttons(
            plan, [], origin["addr"], origin["addr"], mode="free",
            return_to_origin=return_to_origin)

        appt_info = {
            "name": origin["name"], "addr": origin["addr"],
            "window_start": window_start, "window_end": window_end,
            "radius": radius, "priority_n": priority_n,
            "return_to_origin": return_to_origin,
        }
        origin_tag = origin["name"][:20].replace(" ", "_").replace("/", "_")
        html = render_html(plan, 0, [], appt_info, owner, now,
                           mode="free", origin=origin, gmaps_buttons=gmaps_buttons,
                           return_travel=return_travel)
        filename = f"訪問プラン_{origin_tag}_{now}.html"

    out_dir = os.path.join(BASE, "_output", "route_plans")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(html)

    print(f"\n✅ 出力: {out_path}")
    print("  ブラウザで開いて確認してください。")
    return out_path


if __name__ == "__main__":
    main()
