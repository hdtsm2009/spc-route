"""フェーズ0.4c: 番地なしスポカフェ掲載店を「店名＋市区町村」でPOIジオコーディング。

スポカフェ掲載店マスタには番地が無く、国土地理院（住所専用）では町名の中心点しか取れない
（→フェーズ04b で72件のみB精度）。本スクリプトは Google Places Text Search で
「店名＋市区町村」から**実在店舗の座標**を引き、精度Aで補完する。

無料の Nominatim(OSM) は日本の飲食店POIをほぼ持たず当たり率2.5%で使い物にならないため、
Google Places API（要APIキー・従量課金）を使う。

事前準備:
  - 環境変数 GOOGLE_MAPS_API_KEY に Places API 有効のキーを設定。
    PowerShell:  $env:GOOGLE_MAPS_API_KEY = "AIza..."
  - キャッシュ(_data/places_cache.json)するため再実行は無料・再開可能。

入力 : 統合店舗マスタ.csv（未マッチ判定）, 店舗一覧マスタ_*.txt（掲載店マスタ）
出力 : _output/補完_スポカフェ掲載店_POI.csv（geocoded.csv と同じ列構成・精度A）

実行順: 統合マスタ構築 → ジオコーディング → 04b(地名概算) → [本スクリプト 04c] → フェーズ05 → エクスポート
        フェーズ05 は POI(04c) を優先し、POIで取れなかった店だけ地名概算(04b)で補う。
"""
import os
import re
import csv
import sys
import glob
import json
import time
import hashlib

import requests

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
MASTER_DIR = os.path.join(ROOT, "_マスタデータ")
INTG_CSV = os.path.join(BASE, "_output", "統合店舗マスタ.csv")
GEOCODED_CSV = os.path.join(BASE, "_output", "統合店舗マスタ_geocoded.csv")
OUT_CSV = os.path.join(BASE, "_output", "補完_スポカフェ掲載店_POI.csv")
CACHE = os.path.join(BASE, "_data", "places_cache.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 統合マスタ構築 import name_keys  # noqa: E402

def _load_api_key() -> str:
    """APIキーを 環境変数 → ローカルキーファイルの順で取得。
    キーファイル(_config/google_places_key.local)は .gitignore 済みで安全。"""
    k = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if k:
        return k
    keyfile = os.path.join(BASE, "_config", "google_places_key.local")
    if os.path.exists(keyfile):
        for line in open(keyfile, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return ""


API_KEY = _load_api_key()
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = "places.location,places.formattedAddress,places.displayName"
SLEEP = 0.06
PREC_POI = "詳細(POI店名一致)"   # フェーズ05 の geo_quality_rank で A 判定（"詳細"を含む）


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_cache(c):
    with open(CACHE, "w", encoding="utf-8") as fp:
        json.dump(c, fp, ensure_ascii=False)


def clean_name(n: str) -> str:
    """末尾の英語別名カッコ等を落として検索精度を上げる。"""
    n = re.sub(r"\(.*$", "", n)          # "店名(Sports bar...)" の後半を除去
    n = re.sub(r"｜.*$", "", n)          # 全角パイプ以降
    return n.strip()


def places_text_search(query: str, session, cache):
    """Google Places Text Search。(lat,lng,formatted) or None。キャッシュ利用。"""
    if query in cache:
        c = cache[query]
        if not c:
            return None
        return c["lat"], c["lng"], c.get("addr", "")
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {"textQuery": query, "languageCode": "ja", "regionCode": "JP"}
    lat = lng = None
    addr = ""
    try:
        r = session.post(PLACES_URL, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        data = r.json()
        places = data.get("places", [])
        if places:
            loc = places[0].get("location", {})
            lat = loc.get("latitude")
            lng = loc.get("longitude")
            addr = places[0].get("formattedAddress", "")
    except Exception as e:
        cache[query] = None
        print(f"  ! {type(e).__name__}: {str(e)[:80]}")
        time.sleep(SLEEP)
        return None
    cache[query] = ({"lat": lat, "lng": lng, "addr": addr} if lat is not None else None)
    time.sleep(SLEEP)
    return (lat, lng, addr) if lat is not None else None


def load_keizai_unmatched():
    """店舗一覧マスタの 掲載 かつ 統合マスタ未マッチ レコードを返す。"""
    intg = set()
    for r in csv.DictReader(open(INTG_CSV, encoding="utf-8-sig")):
        intg |= name_keys(r.get("店名", ""))
    path = sorted(glob.glob(os.path.join(MASTER_DIR, "店舗一覧マスタ_*.txt")))[-1]
    lines = open(path, encoding="utf-8").read().splitlines()
    out = []
    i = 2
    while i + 2 <= len(lines):
        name = lines[i + 1]
        cols = lines[i + 2].split("\t")
        i += 3
        if len(cols) <= 10 or cols[10].strip() != "掲載":
            continue
        if name_keys(name) & intg:
            continue
        out.append({
            "店名": name,
            "都道府県": cols[1].strip() if len(cols) > 1 else "",
            "市区町村": cols[2].strip() if len(cols) > 2 else "",
            "プラン": cols[7].strip() if len(cols) > 7 else "",
        })
    return out


def store_id(name, pref, city):
    h = hashlib.md5(f"{name}|{pref}{city}".encode("utf-8")).hexdigest()[:10]
    return "SPM" + h


def within_city(formatted: str, pref: str, city: str) -> bool:
    """誤った市区町村への一致を弾く。市区町村は「名古屋市,中区」のようにカンマ連結
    （市＋区/地名タグ）なので、全トークンが住所に含まれることを要求する。
    これで政令市の『区違い』（例: 中区→中村区）も弾ける。"""
    if not formatted:
        return False
    toks = [t.strip() for t in re.split(r"[,，]", city or "") if t.strip()]
    if not toks:
        return False
    return all(t in formatted for t in toks)


def main():
    if not API_KEY:
        print("❌ APIキーが見つかりません。次のどちらかで設定してください:")
        print("   (推奨) _config/google_places_key.local にキー文字列だけを保存")
        print('   または PowerShell:  $env:GOOGLE_MAPS_API_KEY = "AIza..."')
        sys.exit(1)

    with open(GEOCODED_CSV, encoding="utf-8-sig") as fp:
        fieldnames = csv.DictReader(fp).fieldnames

    targets = load_keizai_unmatched()
    print(f"未マッチ掲載店（POI対象）: {len(targets)} 件")

    cache = load_cache()
    session = requests.Session()

    out_rows = []
    hit = miss = wrongcity = 0
    for k, rec in enumerate(targets, 1):
        pref, city = rec["都道府県"], rec["市区町村"]
        q = f"{clean_name(rec['店名'])} {city} {pref}"
        res = places_text_search(q, session, cache)
        if not res:
            miss += 1
        else:
            lat, lng, addr = res
            if not within_city(addr, pref, city):
                wrongcity += 1
            else:
                hit += 1
                row = {fn: "" for fn in fieldnames}
                row.update({
                    "店舗ID": store_id(rec["店名"], pref, city),
                    "店名": rec["店名"],
                    "住所": addr,
                    "ソース": "スポカフェマスタ補完(POI)",
                    "スポカフェ掲載": "○",
                    "スポカフェプラン": rec["プラン"],
                    "ファンスタ掲載": "",
                    "営業ターゲット": "★",
                    "緯度": lat,
                    "経度": lng,
                    "ジオコーディング精度": PREC_POI,
                })
                out_rows.append(row)
        if k % 100 == 0:
            save_cache(cache)
            print(f"  {k}/{len(targets)} 採用{hit} 別市除外{wrongcity} 不一致{miss}")

    save_cache(cache)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    print("=" * 50)
    print(f"出力: {OUT_CSV}")
    print(f"  POIで実座標を取得（精度A）: {hit}")
    print(f"  別の市区町村に一致→除外    : {wrongcity}")
    print(f"  見つからず                  : {miss}")
    print(f"  当たり率                    : {hit / max(len(targets),1) * 100:.1f}%")


if __name__ == "__main__":
    main()
