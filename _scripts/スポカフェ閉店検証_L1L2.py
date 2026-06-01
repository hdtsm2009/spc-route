"""スポカフェ閉店候補の多段検証 L1+L2（Google二重検証）。

入力: places_enrich_cache.json で閉業/臨時休業フラグの付いた掲載店（約165件）
処理:
  L1 = searchText 再クエリ（複数結果を走査）。同名(displayName)×同市(formattedAddress)の結果を探し、
       ・同名同市で OPERATIONAL の店が在る → 閉店は疑わしい（営業中の同名店が存在）
       ・同名同市が CLOSED のみ → 閉店を支持
       ・同名同市が一つも無い → 別店誤一致（元の閉店フラグは無関係な店）
  L2 = 上記で得た place_id を Place Details で叩き businessStatus を権威的に再取得
確信度:
  高 = 同名同市CLOSED ＋ L2もCLOSED（営業中の同名店なし）
  中 = 閉店を1本のみ支持
  低 = 別店誤一致 / 同名同市に営業中店あり（→削除候補から外す）
need_L3 = 有料プラン or 口コミ≥200（重要店は別途Web独立確認に回す）

出力: _output/_閉店検証_L1L2.csv
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
ENRICH_CACHE = os.path.join(BASE, "_data", "places_enrich_cache.json")
VERIFY_CACHE = os.path.join(BASE, "_data", "places_verify_cache.json")
OUT_CSV = os.path.join(BASE, "_output", "_閉店検証_L1L2.csv")

_spec = importlib.util.spec_from_file_location(
    "p04c", os.path.join(BASE, "_scripts", "フェーズ04c_店名POIジオコーディング.py"))
p04c = importlib.util.module_from_spec(_spec)
sys.path.insert(0, os.path.join(BASE, "_scripts"))
_spec.loader.exec_module(p04c)

API_KEY = p04c.API_KEY
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/"
SLEEP = 0.06
REVIEW_IMPORTANT = 200  # この口コミ数以上は重要店としてL3対象


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\s　・,，。.\-_/|｜()（）'’\"]", "", s)


def name_match(disp, store):
    """displayName と店名の緩い一致。短い方が長い方に含まれる or 主要トークン一致。"""
    a, b = norm(disp), norm(p04c.clean_name(store))
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # 先頭6文字程度の一致でも可（英語別名カッコ落とし後）
    return a[:6] == b[:6] and len(a) >= 4


def load_cache(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def save_cache(path, c):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(c, fp, ensure_ascii=False)


def search_multi(query, session, cache):
    """複数結果を返す（id, displayName, formattedAddress, businessStatus）。"""
    key = "S::" + query
    if key in cache:
        return cache[key]
    headers = {
        "Content-Type": "application/json", "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": ("places.id,places.displayName,places.formattedAddress,"
                             "places.businessStatus,places.userRatingCount,places.rating"),
    }
    out = []
    try:
        r = session.post(SEARCH_URL, headers=headers,
                         json={"textQuery": query, "languageCode": "ja", "regionCode": "JP"}, timeout=15)
        r.raise_for_status()
        for p in r.json().get("places", [])[:10]:
            out.append({
                "id": p.get("id", ""),
                "name": (p.get("displayName") or {}).get("text", ""),
                "addr": p.get("formattedAddress", ""),
                "status": p.get("businessStatus", ""),
                "reviews": p.get("userRatingCount"),
            })
    except Exception as e:
        print(f"  ! search {type(e).__name__}: {str(e)[:60]}")
    cache[key] = out
    time.sleep(SLEEP)
    return out


def details_status(place_id, session, cache):
    key = "D::" + place_id
    if key in cache:
        return cache[key]
    headers = {"X-Goog-Api-Key": API_KEY,
               "X-Goog-FieldMask": "id,displayName,formattedAddress,businessStatus"}
    st = ""
    try:
        r = session.get(DETAILS_URL + place_id, headers=headers, timeout=15)
        r.raise_for_status()
        st = r.json().get("businessStatus", "")
    except Exception as e:
        print(f"  ! details {type(e).__name__}: {str(e)[:60]}")
    cache[key] = st
    time.sleep(SLEEP)
    return st


def main():
    if not API_KEY:
        print("❌ APIキーなし"); sys.exit(1)

    enrich = load_cache(ENRICH_CACHE)
    # 閉店候補の母集団を 店舗ID付きで（同市判定に都道府県/市区町村が要る）
    targets = []
    for rec in p04c.load_keizai_unmatched():
        # load_keizai_unmatched は ID を持たないので再付与のため別ローダを使う
        pass
    # 店舗ID付きローダ（閉店リスト生成と同じ）
    import glob
    intg = set()
    for r in csv.DictReader(open(p04c.INTG_CSV, encoding="utf-8-sig")):
        intg |= p04c.name_keys(r.get("店名", ""))
    path = sorted(glob.glob(os.path.join(ROOT, "_マスタデータ", "店舗一覧マスタ_*.txt")))[-1]
    lines = open(path, encoding="utf-8").read().splitlines()
    i = 2
    cands = []
    while i + 2 <= len(lines):
        sid, name, cols = lines[i].strip(), lines[i + 1], lines[i + 2].split("\t")
        i += 3
        if len(cols) <= 10 or cols[10].strip() != "掲載":
            continue
        if p04c.name_keys(name) & intg:
            continue
        pref = cols[1].strip() if len(cols) > 1 else ""
        city = cols[2].strip() if len(cols) > 2 else ""
        plan = cols[7].strip() if len(cols) > 7 else ""
        q = f"{p04c.clean_name(name)} {city} {pref}"
        v = enrich.get(q)
        if v and v.get("status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            cands.append({"店舗ID": sid, "店名": name, "都道府県": pref, "市区町村": city,
                          "プラン": plan, "query": q, "enrich_status": v.get("status"),
                          "評価": v.get("rating") or "", "口コミ数": v.get("reviews") or ""})

    print(f"閉店候補: {len(cands)} 件 を L1+L2 検証")
    vcache = load_cache(VERIFY_CACHE)
    session = requests.Session()
    rows = []
    for k, c in enumerate(cands, 1):
        results = search_multi(c["query"], session, vcache)
        # 同名×同市の結果を抽出
        same = [r for r in results if name_match(r["name"], c["店名"])
                and p04c.within_city(r["addr"], c["都道府県"], c["市区町村"])]
        oper_same = [r for r in same if r["status"] == "OPERATIONAL"]
        closed_same = [r for r in same if r["status"] in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")]
        # L2: 同名同市CLOSED の place_id を権威確認（無ければ検索1位）
        pid = (closed_same[0]["id"] if closed_same else (same[0]["id"] if same else
               (results[0]["id"] if results else "")))
        l2 = details_status(pid, session, vcache) if pid else ""

        if not same:
            conf = "低"; note = "同名×同市の一致なし（別店誤検出疑い）"
        elif oper_same:
            conf = "低"; note = "同市に営業中の同名店あり（閉店疑わしい）"
        elif closed_same and l2 in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            conf = "高"; note = "同名同市CLOSED＋Details一致"
        else:
            conf = "中"; note = "閉店支持1本のみ"

        important = (c["プラン"] and c["プラン"] != "フリー") or \
                    (str(c["口コミ数"]).isdigit() and int(c["口コミ数"]) >= REVIEW_IMPORTANT)
        rows.append({
            "店舗ID": c["店舗ID"], "店名": c["店名"], "プラン": c["プラン"],
            "都道府県": c["都道府県"], "市区町村": c["市区町村"],
            "enrich判定": "閉業" if c["enrich_status"] == "CLOSED_PERMANENTLY" else "臨時休業",
            "L1同名同市": "○" if same else "×",
            "L1営業中同名店": "有" if oper_same else "",
            "L2Details": l2,
            "確信度": conf, "判定根拠": note,
            "need_L3": "○" if (important and conf != "低") else "",
            "評価": c["評価"], "口コミ数": c["口コミ数"],
        })
        if k % 30 == 0:
            save_cache(VERIFY_CACHE, vcache)
            print(f"  {k}/{len(cands)}")
    save_cache(VERIFY_CACHE, vcache)

    order = {"高": 0, "中": 1, "低": 2}
    rows.sort(key=lambda r: (order[r["確信度"]],
                             0 if (r["プラン"] and r["プラン"] != "フリー") else 1))
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    import collections
    by = collections.Counter(r["確信度"] for r in rows)
    l3 = sum(1 for r in rows if r["need_L3"])
    print("=" * 56)
    print(f"出力: {OUT_CSV}")
    print(f"  確信度 高:{by['高']} 中:{by['中']} 低(誤検出疑い):{by['低']}")
    print(f"  L3(Web独立確認)対象の重要店: {l3} 件")
    print("--- 確信度『低』＝閉店フラグを外すべき誤検出疑い 上位 ---")
    for r in [r for r in rows if r["確信度"] == "低"][:10]:
        print(f"  {r['判定根拠']:28} {r['店名'][:22]} ({r['市区町村']})")


if __name__ == "__main__":
    main()
