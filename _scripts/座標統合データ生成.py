# -*- coding: utf-8 -*-
"""スポカフェ管理画面エクスポート(shops_*.csv)の掲載店に緯度経度を統合する。

座標の付け方（出所の優先順位）:
  1) GSI住所ジオコーディング … 今回手に入った番地付き住所を国土地理院API(無料)で変換。一次情報ベースで最も正確。
  2) Google補完座標(既存)     … GSIが失敗した店は、既納品のGoogle Places補完CSVの座標で救済。
  3) 失敗                      … 上記いずれも取れない店（手当て対象）。

QA列として、GSIとGoogleの両方が取れた店は両者の距離(m)を出し、ズレの大きい店を炙り出せるようにする。

出力: _output/座標統合データ_YYYYMMDD.csv （UTF-8 BOM・店舗IDでJOINして緯度/経度を還元）
"""
import os
import csv
import json
import math
import time
import urllib.parse

import requests

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
EXPORT = r"C:\Users\hdtsm\Downloads\shops_20260602_135027.csv"
GP_CSV = os.path.join(BASE, "_納品_スポカフェ掲載店住所還元_20260530",
                      "スポカフェ掲載店_住所補完データ_20260530.csv")
CACHE = os.path.join(BASE, "_data", "geocode_cache.json")
OUT = os.path.join(BASE, "_output", "座標統合データ_20260602.csv")

API = "https://msearch.gsi.go.jp/address-search/AddressSearch"
SLEEP = 0.12
SAVE_EVERY = 100


def col(header, name):
    for i, h in enumerate(header):
        if h.strip() == name:
            return i
    return -1


def haversine_m(la1, ln1, la2, ln2):
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(ln2 - ln1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_cache(cache):
    with open(CACHE, "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False)


def geocode(addr, session, cache):
    if addr in cache:
        v = cache[addr]
        return v.get("lat"), v.get("lng"), v.get("prec")
    url = API + "?q=" + urllib.parse.quote(addr)
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return None, None, f"error:{type(e).__name__}"
    if not data:
        cache[addr] = {"lat": None, "lng": None, "prec": "failed"}
        return None, None, "failed"
    feat = data[0]
    lng, lat = feat["geometry"]["coordinates"]
    title = feat.get("properties", {}).get("title", "")
    prec = "詳細" if any(c in title for c in "0123456789０１２３４５６７８９丁目") else "町名"
    cache[addr] = {"lat": lat, "lng": lng, "prec": prec}
    time.sleep(SLEEP)
    return lat, lng, prec


def main():
    # --- 管理画面エクスポート（掲載店のみ・ID重複は先勝ち） ---
    rows = list(csv.reader(open(EXPORT, encoding="utf-8-sig")))
    eh = rows[0]
    ci = {k: col(eh, k) for k in ["店舗ID", "店舗名", "郵便番号", "住所", "建物", "プラン", "ステータス"]}
    shops = {}
    for r in rows[1:]:
        if len(r) <= ci["ステータス"] or r[ci["ステータス"]].strip() != "掲載":
            continue
        sid = r[ci["店舗ID"]].strip()
        if sid in shops:
            continue
        shops[sid] = {
            "店名": r[ci["店舗名"]].strip(),
            "郵便番号": r[ci["郵便番号"]].strip(),
            "住所": r[ci["住所"]].strip(),
            "建物": r[ci["建物"]].strip(),
            "プラン": r[ci["プラン"]].strip(),
        }
    print("掲載店(ユニークID):", len(shops))

    # --- Google補完座標（ID→lat,lng） ---
    gp = list(csv.reader(open(GP_CSV, encoding="utf-8-sig")))
    gh = gp[0]
    gi_id, gi_lat, gi_lng = col(gh, "スポカフェ店舗ID"), col(gh, "緯度"), col(gh, "経度")
    gmap = {}
    for r in gp[1:]:
        try:
            gmap[r[gi_id].strip()] = (float(r[gi_lat]), float(r[gi_lng]))
        except (ValueError, IndexError):
            pass
    print("Google補完座標:", len(gmap))

    cache = load_cache()
    session = requests.Session()
    out_rows = []
    n_gsi = n_google = n_fail = 0
    for idx, (sid, s) in enumerate(shops.items()):
        lat = lng = None
        src = "失敗"
        prec = ""
        gsi_lat, gsi_lng, gsi_prec = (None, None, "")
        if s["住所"]:
            gsi_lat, gsi_lng, gsi_prec = geocode(s["住所"], session, cache)
        if gsi_lat is not None:
            lat, lng, src, prec = gsi_lat, gsi_lng, "GSI住所", gsi_prec
            n_gsi += 1
        elif sid in gmap:
            lat, lng, src, prec = gmap[sid][0], gmap[sid][1], "Google補完", "Google"
            n_google += 1
        else:
            n_fail += 1
        # QA: GSIとGoogleの距離
        dist = ""
        if gsi_lat is not None and sid in gmap:
            dist = round(haversine_m(gsi_lat, gsi_lng, gmap[sid][0], gmap[sid][1]))
        out_rows.append([sid, s["店名"], s["郵便番号"], s["住所"], s["建物"], s["プラン"],
                         lat if lat is not None else "",
                         lng if lng is not None else "",
                         src, prec, dist])
        if (idx + 1) % SAVE_EVERY == 0:
            save_cache(cache)
            print(f"  {idx+1}/{len(shops)} 処理 (GSI {n_gsi} / Google {n_google} / 失敗 {n_fail})")
    save_cache(cache)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["スポカフェ店舗ID", "店名", "郵便番号", "住所", "建物", "プラン",
                    "緯度", "経度", "座標の出所", "精度", "GSI_Google距離m"])
        w.writerows(out_rows)
    print("=" * 40)
    print("出力:", OUT)
    print(f"GSI住所 {n_gsi} / Google補完 {n_google} / 失敗 {n_fail} / 計 {len(shops)}")
    # QAサマリ
    far = [r for r in out_rows if isinstance(r[10], int) and r[10] >= 150]
    print(f"GSIとGoogleが150m以上ズレ(要QA): {len(far)} 件")


if __name__ == "__main__":
    main()
