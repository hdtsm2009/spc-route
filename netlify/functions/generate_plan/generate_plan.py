"""Netlify Function: generate_plan
POST /.netlify/functions/generate_plan

Request body (JSON):
  area          : str  - プリセット名の部分一致 (例: "西日暮里エリア")
  owner         : str  - 担当者名
  window_start  : str  - 活動開始時刻 "HH:MM" (default "17:00")
  window_end    : str  - 活動終了時刻 "HH:MM" (default "21:00")
  max           : int  - 最大訪問件数 (default 7)
  priority_n    : int  - メイン推奨件数 (default 6)
  return_to_origin : bool (default false)

Response:
  200: text/html  - 生成された訪問プランHTML
  400: text/plain - パラメータエラー
  500: text/plain - サーバーエラー
"""
import json
import os
import math
import re
import unicodedata
import datetime
import urllib.parse

_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── データ読み込み ───────────────────────────────────────────────────────────

def _load_json(filename: str):
    with open(os.path.join(_DIR, filename), encoding="utf-8") as f:
        return json.load(f)

# ─── 位置計算 ─────────────────────────────────────────────────────────────────

def haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def walk_minutes(dist_m: float, speed: float) -> int:
    return math.ceil(dist_m / speed)

# ─── 時刻ユーティリティ ───────────────────────────────────────────────────────

def _t2min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _min2t(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"

# ─── 候補絞り込み ─────────────────────────────────────────────────────────────

def check_open(hours_str: str, ws: str, we: str) -> str:
    if not hours_str or hours_str.strip() in ("", "情報なし"):
        return "要確認"
    h = unicodedata.normalize("NFKC", hours_str)
    if any(kw in h for kw in ["定休", "閉店", "営業なし"]):
        return "要確認"
    start_h = int(ws.split(":")[0])
    end_h = int(we.split(":")[0])
    hours_found = [int(hh) for hh, _ in re.findall(r"(\d{1,2})[：:時](\d{0,2})", h)
                   if hh.isdigit()]
    if not hours_found:
        return "要確認"
    open_h, close_h = min(hours_found), max(hours_found)
    if close_h <= 5:
        close_h += 24
    if close_h <= start_h or open_h >= end_h:
        return "除外"
    return "開いている可能性高"


def filter_candidates(rows, origin_lat, origin_lng, radius_m,
                      window_start, window_end, geocoding_cfg):
    geo_ok = geocoding_cfg.get("route_eligible", ["A", "B"])
    result = []
    for r in rows:
        if str(r.get("sales_status", "")).strip() == "NG":
            continue
        if str(r.get("スポカフェ掲載", "")).strip() == "○":
            continue
        try:
            lat = float(r.get("緯度") or "")
            lng = float(r.get("経度") or "")
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

# ─── スコア取得 ───────────────────────────────────────────────────────────────

def get_score(r: dict) -> int:
    if r.get("営業スコア") not in (None, ""):
        try:
            return int(float(r["営業スコア"]))
        except (ValueError, TypeError):
            pass
    return {"S": 70, "A": 50, "B": 30, "C": 10}.get(r.get("営業ランク", ""), 20)

# ─── ルート組み立て ───────────────────────────────────────────────────────────

def _fill_block(start_min, end_min, cur_lat, cur_lng,
                pool, used_ids, route_cfg):
    speed = route_cfg["walk_speed_m_per_min"]
    stay = route_cfg["stay_minutes_per_store"]
    max_leg = route_cfg.get("max_leg_walk_min", 999)
    budget = route_cfg.get("_budget", 99)  # caller sets this
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
            continue
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


def build_free_route(candidates, origin_lat, origin_lng,
                     window_start, window_end, max_stores,
                     return_to_origin, route_cfg):
    stay = route_cfg["stay_minutes_per_store"]
    speed = route_cfg["walk_speed_m_per_min"]
    pool = sorted(candidates, key=lambda r: (-get_score(r), r["_dist_m"]))
    used = set()
    cfg_copy = dict(route_cfg)
    cfg_copy["_budget"] = max_stores
    plan, _, cur_lat, cur_lng = _fill_block(
        _t2min(window_start), _t2min(window_end),
        float(origin_lat), float(origin_lng), pool, used, cfg_copy)
    return_travel = 0
    if return_to_origin and plan:
        return_travel = walk_minutes(
            haversine_m(cur_lat, cur_lng, float(origin_lat), float(origin_lng)), speed)
    return plan, return_travel

# ─── Google Maps リンク ───────────────────────────────────────────────────────

_GMAPS_MAX = 9


def maps_link(addr: str, label: str = "") -> str:
    q = urllib.parse.quote(addr or label or "")
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def nav_link(from_addr: str, to_addr: str) -> str:
    return (f"https://www.google.com/maps/dir/"
            f"{urllib.parse.quote(from_addr)}/{urllib.parse.quote(to_addr)}")


def _addr_of(r: dict) -> str:
    try:
        return f"{float(r.get('緯度') or '')},{float(r.get('経度') or '')}"
    except (ValueError, TypeError):
        return r.get("住所", "")


def _route_urls(waypoints: list) -> list:
    if len(waypoints) < 2:
        return []
    urls, step = [], _GMAPS_MAX - 1
    for i in range(0, len(waypoints) - 1, step):
        chunk = waypoints[i:i + _GMAPS_MAX]
        urls.append("https://www.google.com/maps/dir/" +
                     "/".join(urllib.parse.quote(w) for w in chunk))
    return urls


def build_gmaps_buttons(pre_plan, post_plan, appt_addr, origin_addr,
                        mode="free", return_to_origin=False):
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

# ─── 訪問動機サマリー ─────────────────────────────────────────────────────────

def _visit_pitch(r: dict) -> str:
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

# ─── HTML 定数・テンプレート ──────────────────────────────────────────────────

RANK_COLOR = {"S": "#e74c3c", "A": "#e67e22", "B": "#3498db", "C": "#7f8c8d", "除外": "#bdc3c7"}
OPEN_ICON  = {"開いている可能性高": "🟢", "要確認": "🟡", "除外": "🔴"}

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
  .score-plus{color:#27ae60;font-weight:bold;text-align:right}
  .score-minus{color:#e74c3c;font-weight:bold;text-align:right}
  .pitch-chip{display:inline-block;background:#eaf6fb;color:#1a6a8a;font-size:10px;
              padding:1px 6px;border-radius:8px;border:1px solid #aad4e8;margin-top:2px}
  .chain-badge{display:inline-block;background:#fff3cd;color:#856404;font-size:10px;
               padding:1px 5px;border-radius:3px;border:1px solid #ffc107;margin-left:4px;
               vertical-align:middle;font-weight:bold}
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
  const cmd=btn.dataset.cmd;
  navigator.clipboard.writeText(cmd).then(()=>{
    const t=document.getElementById('copy-toast');
    t.textContent='コピーしました';t.style.display='block';
    setTimeout(()=>{t.style.display='none'},2000);
  });
}
function toggleExclude(btn){
  const row=btn.closest('tr');
  const excl=!row.classList.contains('row-excluded');
  row.classList.toggle('row-excluded',excl);
  btn.textContent=excl?'↩ 戻す':'✕ 除外';
  updateExcludeCount();
  rebuildRoutes();
}
function updateExcludeCount(){
  const n=document.querySelectorAll('tr.store-row.row-excluded').length;
  const b=document.getElementById('exclude-badge');
  if(b) b.textContent=n>0?`除外中: ${n}件`:'';
}
function resetExclusions(){
  document.querySelectorAll('tr.store-row.row-excluded').forEach(row=>{
    row.classList.remove('row-excluded');
    const btn=row.querySelector('.exclude-btn');
    if(btn) btn.textContent='✕ 除外';
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

_MANUAL_HTML = """
<div id="tab-manual" style="display:none">
<div class="manual-wrap">
<h2 class="manual-h2">📖 使い方ガイド</h2>
<div class="manual-grid">
<div class="manual-card">
<h3 class="manual-h3">🚀 基本の使い方（3ステップ）</h3>
<ol class="manual-ol">
  <li><strong>Google Mapsボタンをタップ</strong><br>周遊ルートをワンタップで開けます。</li>
  <li><strong>不要な店舗は「✕ 除外」</strong><br>除外するとGoogle Mapsのルートから自動で外れます。「↩ 戻す」で復元可。</li>
  <li><strong>予備候補は「▲ 隠す」で折りたたみ</strong><br>隠すとGoogle Mapsルートからも外れます。</li>
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
</div>
<div class="manual-card">
<h3 class="manual-h3">🏷 バッジの見方</h3>
<table class="manual-tbl">
  <tr><th>バッジ</th><th>意味</th></tr>
  <tr><td><span style="background:#eaf6fb;color:#1a6a8a;font-size:11px;padding:1px 6px;border-radius:8px;border:1px solid #aad4e8">両サービス未掲載</span></td><td>スポカフェ・ファンスタ両方未掲載。最優先ターゲット</td></tr>
  <tr><td><span style="background:#eaf6fb;color:#1a6a8a;font-size:11px;padding:1px 6px;border-radius:8px;border:1px solid #aad4e8">スポーツバー◎</span></td><td>スポーツバー・PV業態。訪問理由がそのままトークになる</td></tr>
  <tr><td><span style="background:#fff3cd;color:#856404;font-size:11px;padding:1px 5px;border-radius:3px;border:1px solid #ffc107;font-weight:bold">FC?</span></td><td>フリーダイヤルからチェーン・本部系の可能性あり。確定判定ではありません。</td></tr>
</table>
<p class="manual-note">FC? はあくまで補助表示です。訪問前に確認することをお勧めします。</p>
</div>
</div></div></div>
"""

# ─── HTML 行・詳細レンダリング ────────────────────────────────────────────────

def _detail_html(r: dict) -> str:
    hp = r.get("HP", "")
    hp_cell = f'<a href="{hp}" target="_blank">{hp[:50]}</a>' if hp and hp.startswith("http") else hp
    sales_fields = [
        ("評価", r.get("評価")), ("口コミ数", r.get("口コミ数")),
        ("営業時間", r.get("営業時間")), ("予算", r.get("予算")),
        ("最寄駅", r.get("最寄駅")), ("HP", hp_cell), ("SNS", r.get("SNS")),
    ]
    def tbl(fields):
        return "".join(
            f"<tr><td class='dl'>{lbl}</td><td>{val}</td></tr>"
            for lbl, val in fields if val
        )
    return (f'<details class="store-detail"><summary>詳細</summary>'
            f'<div class="detail-body">'
            f'<table class="detail-tbl">{tbl(sales_fields)}</table>'
            f'</div></details>')


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
      <td><a href="tel:{phone}">{phone}</a></td>
      <td><a href="{nl}" target="_blank">📍</a></td>
      <td>{_detail_html(r)}</td>
      <td class="memo-cell" contenteditable="true"></td>
      <td><button class="rec-btn" onclick="copyCmd(this)" data-cmd="{rec_cmd}">📝記録</button>
          <br><button class="exclude-btn" onclick="toggleExclude(this)">✕ 除外</button></td>
    </tr>"""

# ─── HTML レンダリング ────────────────────────────────────────────────────────

def render_html(plan, appt_info, owner, generated_at, origin, gmaps_buttons, route_cfg):
    priority_n = appt_info.get("priority_n", 6)
    gmaps_buttons = gmaps_buttons or []
    origin = origin or {}
    total = len(plan)
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

    if plan:
        rows_html += '<tr class="section-header"><td colspan="12">── 訪問順 ──</td></tr>'
    for idx, e in enumerate(plan):
        sec = "free-backup" if idx >= priority_n else "free"
        rows_html += maybe_sep()
        rows_html += _store_row_html(e, prev, owner, section=sec)
        prev = e["store"].get("住所", prev)
        store_count += 1

    return_travel = appt_info.get("return_travel", 0)
    if return_travel > 0 and plan:
        rows_html += f"""
        <tr class="appt-move">
          <td>{plan[-1]['depart_time']}</td>
          <td>🚶 徒歩{return_travel}分</td>
          <td colspan="10">→ 起点へ戻る（{origin.get('name', '')}）</td>
        </tr>"""

    gmaps_html = ""
    if gmaps_buttons:
        btns = " ".join(
            f'<a class="gmaps-btn" href="{url}" target="_blank" data-route-type="{rt}">🗺 {lbl}</a>'
            for lbl, url, rt in gmaps_buttons
        )
        gmaps_html = f'<div class="gmaps-bar">{btns}</div>'

    priority_label = (f"推奨{min(priority_n, total)}件 + 予備{max(total - priority_n, 0)}件"
                      if show_sep else f"{total}件")
    return_to_origin = appt_info.get("return_to_origin", False)
    endpoint_label = "起点に戻る" if return_to_origin else "最後の訪問店舗で終了"
    display_name = origin.get("name", appt_info.get("name", ""))
    origin_addr = origin.get("addr", "")
    stay_min = route_cfg.get("stay_minutes_per_store", 15)

    origin_meta = origin_addr.replace('"', '&quot;')
    rto_meta = str(return_to_origin).lower()
    route_meta_html = (
        f'<div id="route-meta" data-mode="free" '
        f'data-origin="{origin_meta}" data-appt-addr="{origin_meta}" '
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
  <strong>β版：営業担当レビュー用</strong>
  <strong>⚠ 社内確認用 — 社外共有禁止</strong>
</div>
<div class="tab-bar">
  <button class="tab-btn active" data-tab="plan" onclick="switchTab('plan')">📍 訪問プラン</button>
  <button class="tab-btn" data-tab="manual" onclick="switchTab('manual')">📖 使い方</button>
</div>
<div id="tab-plan">
<h1>📍 訪問プラン – {display_name}<span id="exclude-badge" class="exclude-badge"></span></h1>
<div class="meta">起点: {origin_addr} ／ 担当: {owner} ／ 生成: {generated_at}</div>
{gmaps_html}
{route_ops_html}
{route_meta_html}
<div class="summary">
  <span>⏱ 活動時間: {appt_info['window_start']}〜{appt_info['window_end']}</span>
  <span>🏁 終了: {endpoint_label}</span>
  <span>📌 訪問予定: {priority_label}</span>
  <span>🔍 抽出半径: {appt_info['radius']}m</span>
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
※ 各店舗の滞在目安は{stay_min}分。営業メモ欄は印刷用（閉じると消えます）。
</p>
</div>
{_MANUAL_HTML}
</body>
</html>"""

# ─── Netlify Function ハンドラ ────────────────────────────────────────────────

def handler(event, context):
    # CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
            "body": "",
        }

    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return {"statusCode": 400, "body": "Invalid JSON"}

    area_name    = body.get("area", "")
    owner        = body.get("owner", "")
    window_start = body.get("window_start", "17:00")
    window_end   = body.get("window_end",   "21:00")
    max_stores   = int(body.get("max", 7))
    priority_n   = int(body.get("priority_n", 6))
    return_to_origin = bool(body.get("return_to_origin", False))

    if not area_name:
        return {"statusCode": 400, "body": "area は必須です"}

    try:
        stores = _load_json("stores.json")
        cfg    = _load_json("config.json")
    except Exception as e:
        return {"statusCode": 500, "body": f"データ読み込みエラー: {e}"}

    presets    = cfg.get("presets", [])
    route_cfg  = cfg.get("route", {})
    geocod_cfg = cfg.get("geocoding", {})

    preset = next((p for p in presets if area_name in p["name"]), None)
    if not preset:
        names = [p["name"] for p in presets]
        return {"statusCode": 400,
                "body": f'エリア "{area_name}" が見つかりません。登録済み: {names}'}

    origin = {
        "name": preset["station"],
        "addr": preset["station"],
        "lat":  preset["lat"],
        "lng":  preset["lng"],
    }
    radius = preset.get("radius_m", route_cfg.get("search_radius_m", 1200))

    try:
        cands = filter_candidates(
            stores, origin["lat"], origin["lng"], radius,
            window_start, window_end, geocod_cfg)

        plan, return_travel = build_free_route(
            cands, origin["lat"], origin["lng"],
            window_start, window_end, max_stores,
            return_to_origin, route_cfg)

        gmaps_buttons = build_gmaps_buttons(
            plan, [], origin["addr"], origin["addr"],
            mode="free", return_to_origin=return_to_origin)

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        appt_info = {
            "name": origin["name"],
            "addr": origin["addr"],
            "window_start": window_start,
            "window_end": window_end,
            "radius": radius,
            "priority_n": priority_n,
            "return_to_origin": return_to_origin,
            "return_travel": return_travel,
        }

        html = render_html(plan, appt_info, owner or "—", now,
                           origin, gmaps_buttons, route_cfg)

    except Exception as e:
        import traceback
        return {"statusCode": 500, "body": f"生成エラー: {e}\n{traceback.format_exc()}"}

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "body": html,
        "isBase64Encoded": False,
    }
