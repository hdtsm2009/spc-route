"""住所・駅名ジオコーディング（国土地理院 AddressSearch API）。

APIキー不要・無料: https://msearch.gsi.go.jp/address-search/AddressSearch
Vercel関数から使うため requests でなく urllib（標準ライブラリ）で実装。

geocode(query) -> {"lat":float,"lng":float,"title":str} または None
"""
import json
import urllib.parse
import urllib.request

_API = "https://msearch.gsi.go.jp/address-search/AddressSearch"


def _select_feature(data, q):
    """GSIは結果を行政コード順で返すため data[0] が最適とは限らない。
    駅名・地名は地方の同名地（例「新宿三丁目駅」→群馬県前橋市新宿）に誤解決しやすいので、
    クエリと完全一致するタイトルを最優先で選ぶ。"""
    qn = (q or "").replace(" ", "").replace("　", "")
    if not qn:
        return data[0]
    exact = [f for f in data
             if f.get("properties", {}).get("title", "").replace(" ", "") == qn]
    if exact:
        return exact[0]
    # クエリ全体（「○○駅」等）を含むタイトルがあれば、最も短い＝余計な接頭辞が少ないものを選ぶ
    contains = [f for f in data
                if qn in f.get("properties", {}).get("title", "").replace(" ", "")]
    if contains:
        return min(contains, key=lambda f: len(f.get("properties", {}).get("title", "")))
    return data[0]


def geocode(query: str):
    """住所・駅名・地名 → 座標。失敗時 None。
    国土地理院APIは「○○駅」も住所も検索可能。"""
    q = (query or "").strip()
    if not q:
        return None
    url = _API + "?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={"User-Agent": "spocafe-route-tool/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not data:
        return None
    feat = _select_feature(data, q)
    try:
        lng, lat = feat["geometry"]["coordinates"]
    except (KeyError, ValueError, TypeError):
        return None
    title = feat.get("properties", {}).get("title", q)
    return {"lat": float(lat), "lng": float(lng), "title": title}
