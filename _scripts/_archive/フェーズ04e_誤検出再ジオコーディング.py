"""フェーズ0.4e: 閉店検証L1で『誤検出(同名同市の一致なし)』となったAS掲載店を再ジオコーディング。

背景: 04cは places[0] を within_city で採用したが店名照合をしておらず、
      "市内の別名店" を拾って座標が誤っていた（L1検証で判明=確信度低の54件）。
方針: 元クエリでは正店が出ないため、クエリ変形＋『店名一致を必須』にして探し直す。
      - 変形1: clean_name + 市区町村 + 都道府県（原型）
      - 変形2: clean_name + 都道府県（区落とし。区タグが緩い掲載店向け）
      - 変形3: 生店名(カッコ含む) + 都道府県
      各結果のうち name_match==True を必須にし、都道府県一致を確認。営業中・口コミ多い順で採用。
確信度:
      高 = 名称一致＋元の市区町村も一致
      中 = 名称一致＋同一都道府県（区は不一致＝マスタ区タグの揺れ）
      不可 = 名称一致の結果が無い（Google上に存在しない/別名/閉店埋没）

出力: _output/_誤検出再ジオコーディング_結果.csv（店舗ID/店名/確信度/新緯度経度/営業状況/採用クエリ）
"""
import os
import re
import csv
import sys
import json
import time
import importlib.util
import unicodedata

import requests

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
L1L2 = os.path.join(BASE, "_output", "_閉店検証_L1L2.csv")
CACHE = os.path.join(BASE, "_data", "places_regeocode_cache.json")
OUT = os.path.join(BASE, "_output", "_誤検出再ジオコーディング_結果.csv")

_spec = importlib.util.spec_from_file_location(
    "p04c", os.path.join(BASE, "_scripts", "フェーズ04c_店名POIジオコーディング.py"))
p04c = importlib.util.module_from_spec(_spec)
sys.path.insert(0, os.path.join(BASE, "_scripts"))
_spec.loader.exec_module(p04c)

API_KEY = p04c.API_KEY
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
SLEEP = 0.06
FM = ("places.id,places.displayName,places.formattedAddress,places.location,"
      "places.businessStatus,places.userRatingCount,places.rating")


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\s　・,，。.\-_/|｜()（）'’\"]", "", s)


def name_match(disp, store):
    a, b = norm(disp), norm(p04c.clean_name(store))
    if not a or not b:
        return False
    return a in b or b in a or (len(a) >= 4 and a[:6] == b[:6])


def load_cache():
    return json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}


def save_cache(c):
    json.dump(c, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)


def search(query, session, cache):
    if query in cache:
        return cache[query]
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": API_KEY,
               "X-Goog-FieldMask": FM}
    out = []
    try:
        r = session.post(SEARCH_URL, headers=headers,
                         json={"textQuery": query, "languageCode": "ja", "regionCode": "JP"}, timeout=15)
        r.raise_for_status()
        for p in r.json().get("places", [])[:10]:
            loc = p.get("location", {})
            out.append({"id": p.get("id", ""), "name": (p.get("displayName") or {}).get("text", ""),
                        "addr": p.get("formattedAddress", ""), "lat": loc.get("latitude"),
                        "lng": loc.get("longitude"), "status": p.get("businessStatus", ""),
                        "reviews": p.get("userRatingCount") or 0, "rating": p.get("rating") or ""})
    except Exception as e:
        print(f"  ! {type(e).__name__}: {str(e)[:60]}")
    cache[query] = out
    time.sleep(SLEEP)
    return out


def main():
    if not API_KEY:
        print("❌ APIキーなし"); sys.exit(1)
    rows = [r for r in csv.DictReader(open(L1L2, encoding="utf-8-sig")) if r["確信度"] == "低"]
    print(f"再ジオコーディング対象（誤検出）: {len(rows)} 件")

    cache = load_cache()
    session = requests.Session()
    out_rows = []
    hi = mid = ng = 0
    for k, r in enumerate(rows, 1):
        name, pref, city = r["店名"], r["都道府県"], r["市区町村"]
        variants = [f"{p04c.clean_name(name)} {city} {pref}",
                    f"{p04c.clean_name(name)} {pref}",
                    f"{name} {pref}"]
        cand = []  # name一致の結果を集める
        for q in variants:
            for res in search(q, session, cache):
                if not res["lat"] or not name_match(res["name"], name):
                    continue
                same_pref = pref and (pref.replace("都", "").replace("府", "").replace("県", "")[:2] in res["addr"]
                                      or pref in res["addr"])
                if not same_pref:
                    continue
                city_ok = p04c.within_city(res["addr"], pref, city)
                cand.append({**res, "city_ok": city_ok, "q": q})
        if not cand:
            ng += 1
            out_rows.append({"店舗ID": r["店舗ID"], "店名": name, "都道府県": pref, "市区町村": city,
                             "プラン": r["プラン"], "確信度": "不可", "新緯度": "", "新経度": "",
                             "営業状況": "", "評価": "", "口コミ数": "", "Places名": "", "採用住所": "", "採用クエリ": ""})
            continue
        # 採用: 市一致優先 → 営業中優先 → 口コミ多い順
        cand.sort(key=lambda c: (0 if c["city_ok"] else 1,
                                 0 if c["status"] == "OPERATIONAL" else 1, -int(c["reviews"] or 0)))
        best = cand[0]
        conf = "高" if best["city_ok"] else "中"
        if conf == "高":
            hi += 1
        else:
            mid += 1
        out_rows.append({"店舗ID": r["店舗ID"], "店名": name, "都道府県": pref, "市区町村": city,
                         "プラン": r["プラン"], "確信度": conf, "新緯度": best["lat"], "新経度": best["lng"],
                         "営業状況": best["status"], "評価": best.get("rating", ""),
                         "口コミ数": best.get("reviews", ""), "Places名": best["name"],
                         "採用住所": best["addr"], "採用クエリ": best["q"]})
        if k % 20 == 0:
            save_cache(cache); print(f"  {k}/{len(rows)} 高{hi} 中{mid} 不可{ng}")
    save_cache(cache)

    cols = ["店舗ID", "店名", "都道府県", "市区町村", "プラン", "確信度",
            "新緯度", "新経度", "営業状況", "評価", "口コミ数", "Places名", "採用住所", "採用クエリ"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=cols); w.writeheader(); w.writerows(out_rows)

    closed_now = sum(1 for r in out_rows if r["営業状況"] in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"))
    print("=" * 56)
    print(f"出力: {OUT}")
    print(f"  復活可 高(店名+市一致): {hi} / 中(店名+県一致): {mid} / 不可(Google上に無し): {ng}")
    print(f"  ※復活分のうち再取得で閉店判明: {closed_now} 件")
    print("--- 復活サンプル ---")
    for r in [r for r in out_rows if r["確信度"] in ("高", "中")][:8]:
        print(f"  [{r['確信度']}] {r['店名'][:22]} → {r['Places名'][:22]} ({r['営業状況']})")


if __name__ == "__main__":
    main()
