"""統合店舗マスタの住所を国土地理院APIで緯度経度に変換する。

- 無料・APIキー不要: https://msearch.gsi.go.jp/address-search/AddressSearch
- 住所単位でキャッシュ（_data/geocode_cache.json）するため、中断しても再開可能。
- 出力: _output/統合店舗マスタ_geocoded.csv

使い方:  python ジオコーディング.py
"""
import os
import csv
import json
import time
import urllib.parse

import requests

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
IN_CSV = os.path.join(BASE, "_output", "統合店舗マスタ.csv")
OUT_CSV = os.path.join(BASE, "_output", "統合店舗マスタ_geocoded.csv")
CACHE = os.path.join(BASE, "_data", "geocode_cache.json")

API = "https://msearch.gsi.go.jp/address-search/AddressSearch"
SLEEP = 0.15  # APIへの負荷配慮
SAVE_EVERY = 100


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_cache(cache):
    with open(CACHE, "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False)


def geocode(addr, session):
    """住所→(lat, lng, 精度). 失敗時は (None, None, 'failed')。
    精度は国土地理院の返すマッチ文字列の細かさで簡易評価。"""
    url = API + "?q=" + urllib.parse.quote(addr)
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return None, None, f"error:{type(e).__name__}"
    if not data:
        return None, None, "failed"
    feat = data[0]
    lng, lat = feat["geometry"]["coordinates"]
    title = feat.get("properties", {}).get("title", "")
    # 丁目・番地までマッチしていれば高精度とみなす
    prec = "詳細" if any(c in title for c in "0123456789０１２３４５６７８９丁目") else "町名"
    return lat, lng, prec


def main():
    rows = list(csv.DictReader(open(IN_CSV, encoding="utf-8-sig")))
    cache = load_cache()
    session = requests.Session()
    session.headers["User-Agent"] = "spocafe-route-tool/0.1"

    addrs = [r["住所"] for r in rows if r["住所"]]
    uniq = sorted(set(addrs))
    todo = [a for a in uniq if a not in cache]
    print(f"全{len(rows)}件 / ユニーク住所{len(uniq)} / 未取得{len(todo)}")

    ok = fail = 0
    for i, a in enumerate(todo, 1):
        lat, lng, prec = geocode(a, session)
        cache[a] = {"lat": lat, "lng": lng, "prec": prec}
        if lat is not None:
            ok += 1
        else:
            fail += 1
        if i % SAVE_EVERY == 0:
            save_cache(cache)
            print(f"  {i}/{len(todo)}  成功{ok} 失敗{fail}")
        time.sleep(SLEEP)
    save_cache(cache)

    # CSVへ反映
    for r in rows:
        c = cache.get(r["住所"])
        if c and c["lat"] is not None:
            r["緯度"] = c["lat"]
            r["経度"] = c["lng"]
            r["ジオコーディング精度"] = c["prec"]
        else:
            r["ジオコーディング精度"] = (c or {}).get("prec", "failed")

    fieldnames = list(rows[0].keys())
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    geocoded = sum(1 for r in rows if r["緯度"])
    print("=" * 50)
    print(f"出力: {OUT_CSV}")
    print(f"  緯度経度付与済: {geocoded}/{len(rows)}")
    print(f"  今回 成功{ok} 失敗{fail}")


if __name__ == "__main__":
    main()
