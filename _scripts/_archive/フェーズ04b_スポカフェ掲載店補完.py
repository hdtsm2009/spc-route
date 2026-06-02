"""フェーズ0.4b: スクレイプに無いスポカフェ掲載店を「地名」概算で補完する。

スポカフェ掲載店マスタ（店舗一覧マスタ_*.txt）には番地が無く、市区町村＋地名タグ
（例「新宿三丁目」「両国」「恵比寿」）しか無い。これらのうち統合マスタに未マッチの
掲載店を、地名タグを国土地理院ジオコーディングに通して「町名レベルの概算位置」で拾う。

重要（正直さ）:
  - これらは店舗の正確な位置ではなく「町名・エリアの中心点」の概算。
  - ジオコーディング精度には "町名(地名概算)" を入れ、UI/ピッチで概算と明示する。
  - 市区町村内に収まる地名のみ採用（別の区に飛ぶ誤マッチを除外）。
  - 町名レベルに解決できた店だけ採用（市の中心点しか取れない店は捨てる）。

入力 : 統合店舗マスタ.csv（未マッチ判定用）, 店舗一覧マスタ_*.txt（掲載店マスタ）
出力 : _output/補完_スポカフェ掲載店.csv（geocoded.csv と同じ列構成）

実行順: 統合マスタ構築 → ジオコーディング → [このスクリプト] → フェーズ05 → エクスポート
"""
import os
import re
import csv
import sys
import glob
import json
import time
import hashlib
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 統合マスタ構築 import name_keys  # noqa: E402

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
MASTER_DIR = os.path.join(ROOT, "_マスタデータ")
INTG_CSV = os.path.join(BASE, "_output", "統合店舗マスタ.csv")
GEOCODED_CSV = os.path.join(BASE, "_output", "統合店舗マスタ_geocoded.csv")
OUT_CSV = os.path.join(BASE, "_output", "補完_スポカフェ掲載店.csv")
CACHE = os.path.join(BASE, "_data", "geocode_cache.json")

API = "https://msearch.gsi.go.jp/address-search/AddressSearch"
SLEEP = 0.15
APPROX_PREC = "町名(地名概算)"   # フェーズ05 の geo_quality_rank で B 判定になる

# 地名タグから捨てるトークン（広域・駅・行政区分の接尾辞のみ等）
_DROP_SUFFIX = ("駅", "駅前", "区", "市", "県", "府", "都", "郡", "町役場")
_BROAD = {"東京", "大阪", "愛知", "神奈川", "兵庫", "京都", "埼玉", "千葉", "福岡",
          "北海道", "宮城", "広島", "静岡", "湘南", "関西", "関東", "都内"}


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_cache(cache):
    with open(CACHE, "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False)


def geocode_raw(addr, session, cache):
    """住所→(lat, lng, title)。キャッシュ利用。失敗時 (None,None,'')。"""
    if addr in cache:
        c = cache[addr]
        return c.get("lat"), c.get("lng"), c.get("title", "")
    url = API + "?q=" + urllib.parse.quote(addr)
    lat = lng = None
    title = ""
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data:
            lng, lat = data[0]["geometry"]["coordinates"]
            title = data[0].get("properties", {}).get("title", "")
    except Exception:
        pass
    cache[addr] = {"lat": lat, "lng": lng, "title": title}
    time.sleep(SLEEP)
    return lat, lng, title


def load_keizai_records():
    """店舗一覧マスタ_*.txt の 状態==掲載 レコードを返す。
    [(店名, 都道府県, 市区町村, 地名, スポーツ, プラン), ...]"""
    files = glob.glob(os.path.join(MASTER_DIR, "店舗一覧マスタ_*.txt"))
    if not files:
        print("❌ 店舗一覧マスタ_*.txt が見つかりません")
        sys.exit(1)
    path = sorted(files)[-1]
    lines = open(path, encoding="utf-8").read().splitlines()
    out = []
    i = 2  # 0:見出し"掲載" 1:列名 から3行1レコード
    while i + 2 <= len(lines):
        name = lines[i + 1]
        cols = lines[i + 2].split("\t")
        i += 3
        if len(cols) <= 10 or cols[10].strip() != "掲載":
            continue
        out.append({
            "店名": name,
            "都道府県": cols[1].strip() if len(cols) > 1 else "",
            "市区町村": cols[2].strip() if len(cols) > 2 else "",
            "地名": cols[3].strip() if len(cols) > 3 else "",
            "スポーツ": cols[4].strip() if len(cols) > 4 else "",
            "プラン": cols[7].strip() if len(cols) > 7 else "",
        })
    print(f"掲載店マスタ: {len(out)} 件")
    return out


def candidate_tokens(rec):
    """地名タグから、ジオコーディングに使える候補トークンを具体的な順で返す。"""
    pref = rec["都道府県"]
    city = rec["市区町村"]
    toks = [t.strip() for t in re.split(r"[ ,，、/／]+", rec["地名"]) if t.strip()]
    good = []
    for t in toks:
        if t in _BROAD:
            continue
        if t.endswith(_DROP_SUFFIX):
            continue
        if t in (pref, city) or t in pref or t in city:
            continue
        if len(t) < 2:
            continue
        good.append(t)
    # 長い（具体的）トークンを優先
    good.sort(key=len, reverse=True)
    # 重複除去（順序保持）
    seen = set()
    return [t for t in good if not (t in seen or seen.add(t))]


def store_id(name, pref, city):
    h = hashlib.md5(f"{name}|{pref}{city}".encode("utf-8")).hexdigest()[:10]
    return "SPM" + h


def main():
    # ── 統合マスタの店名キー（未マッチ判定用） ──
    intg_keys = set()
    for r in csv.DictReader(open(INTG_CSV, encoding="utf-8-sig")):
        intg_keys |= name_keys(r.get("店名", ""))

    # ── geocoded.csv の列構成を踏襲 ──
    with open(GEOCODED_CSV, encoding="utf-8-sig") as fp:
        fieldnames = csv.DictReader(fp).fieldnames

    keizai = load_keizai_records()
    cache = load_cache()
    session = requests.Session()
    session.headers["User-Agent"] = "spocafe-route-tool/0.1"

    out_rows = []
    n_unmatched = n_town = n_city_only = n_notoken = 0
    for k, rec in enumerate(keizai, 1):
        if name_keys(rec["店名"]) & intg_keys:
            continue  # 既に統合マスタにある（番地付きで採用済み）
        n_unmatched += 1

        pref, city = rec["都道府県"], rec["市区町村"]
        prefix = pref + city
        # 市の中心点（比較用基準）
        base_lat, base_lng, _ = geocode_raw(prefix, session, cache)

        toks = candidate_tokens(rec)
        if not toks:
            n_notoken += 1
        hit = None
        for t in toks:
            q = prefix + t
            lat, lng, title = geocode_raw(q, session, cache)
            if lat is None:
                continue
            # 市区町村内に収まる結果のみ採用（別区への誤マッチを除外）
            if not title.startswith(prefix):
                continue
            # 市の中心点と同一座標＝町名へ解決できていない
            if base_lat is not None and abs(lat - base_lat) < 1e-6 and abs(lng - base_lng) < 1e-6:
                continue
            hit = (lat, lng, title, t)
            break

        if not hit:
            n_city_only += 1
            continue
        n_town += 1

        lat, lng, title, tok = hit
        row = {fn: "" for fn in fieldnames}
        row.update({
            "店舗ID": store_id(rec["店名"], pref, city),
            "店名": rec["店名"],
            "業態ジャンル": "",
            "電話番号": "",
            "住所": title,
            "最寄駅": "",
            "営業時間": "",
            "ソース": "スポカフェマスタ補完",
            "スポーツ設備": "",
            "スポカフェ掲載": "○",
            "スポカフェプラン": rec["プラン"],
            "ファンスタ掲載": "",
            "営業ターゲット": "★",
            "緯度": lat,
            "経度": lng,
            "ジオコーディング精度": APPROX_PREC,
        })
        out_rows.append(row)

        if k % 100 == 0:
            save_cache(cache)
            print(f"  {k}/{len(keizai)} 処理  採用{n_town}")

    save_cache(cache)

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    print("=" * 50)
    print(f"出力: {OUT_CSV}")
    print(f"  掲載店のうち未マッチ        : {n_unmatched}")
    print(f"  地名トークン無し            : {n_notoken}")
    print(f"  町名レベルで採用（B・概算） : {n_town}")
    print(f"  市の中心しか取れず除外      : {n_city_only}")


if __name__ == "__main__":
    main()
