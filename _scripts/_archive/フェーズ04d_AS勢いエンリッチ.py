"""フェーズ0.4d: スポカフェ掲載店(AS)の「勢い」エンリッチ（小規模テスト版）。

AS店（スポカフェのみ掲載・1,478件）はマスタ由来で評価・口コミ・価格帯がほぼ空（6〜13%）。
本命なのに「インパクト軸（規模・おいしさ）」が表現できないため、Google Places の
同一 searchText 呼び出しに評価/口コミ/価格帯/営業状況のフィールドを追加して取得する。

04c との違い:
  - FIELD_MASK に rating / userRatingCount / priceLevel / businessStatus を追加（上位SKU課金）
  - 本番の座標キャッシュ(places_cache.json)は触らず、別キャッシュ(places_enrich_cache.json)に保存
  - 結果は _output/_test_AS勢いエンリッチ_sample.csv に書き出して効き目を目視

使い方:
  python _scripts/フェーズ04d_AS勢いエンリッチ.py --limit 300   # 先頭300件で小規模テスト
  python _scripts/フェーズ04d_AS勢いエンリッチ.py --all          # 全1,252件（本番・要課金確認）
"""
import os
import sys
import json
import time
import argparse
import importlib.util

import requests

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
ENRICH_CACHE = os.path.join(BASE, "_data", "places_enrich_cache.json")
OUT_CSV = os.path.join(BASE, "_output", "_test_AS勢いエンリッチ_sample.csv")

# 04c を import して target ローダ・正規化・APIキーを再利用（クエリ文字列を完全一致させる）
_spec = importlib.util.spec_from_file_location(
    "p04c", os.path.join(BASE, "_scripts", "フェーズ04c_店名POIジオコーディング.py"))
p04c = importlib.util.module_from_spec(_spec)
sys.path.insert(0, os.path.join(BASE, "_scripts"))
_spec.loader.exec_module(p04c)

API_KEY = p04c.API_KEY
PLACES_URL = p04c.PLACES_URL
SLEEP = 0.06
# 座標(Basic)＋評価/口コミ/価格/営業状況(Atmosphere=上位SKU)
FIELD_MASK = (
    "places.location,places.formattedAddress,places.displayName,"
    "places.rating,places.userRatingCount,places.priceLevel,"
    "places.businessStatus,places.primaryTypeDisplayName"
)

# priceLevel enum → ¥記号（規模・単価の代理）
PRICE_MAP = {
    "PRICE_LEVEL_FREE": "¥",
    "PRICE_LEVEL_INEXPENSIVE": "¥",
    "PRICE_LEVEL_MODERATE": "¥¥",
    "PRICE_LEVEL_EXPENSIVE": "¥¥¥",
    "PRICE_LEVEL_VERY_EXPENSIVE": "¥¥¥¥",
}


def load_cache():
    if os.path.exists(ENRICH_CACHE):
        with open(ENRICH_CACHE, encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_cache(c):
    with open(ENRICH_CACHE, "w", encoding="utf-8") as fp:
        json.dump(c, fp, ensure_ascii=False)


def enrich_search(query: str, session, cache):
    """評価・口コミ・価格・営業状況つきで取得。dict or None。キャッシュ利用。"""
    if query in cache:
        return cache[query]
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {"textQuery": query, "languageCode": "ja", "regionCode": "JP"}
    out = None
    try:
        r = session.post(PLACES_URL, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        places = r.json().get("places", [])
        if places:
            p = places[0]
            loc = p.get("location", {})
            out = {
                "lat": loc.get("latitude"),
                "lng": loc.get("longitude"),
                "addr": p.get("formattedAddress", ""),
                "rating": p.get("rating"),
                "reviews": p.get("userRatingCount"),
                "price": p.get("priceLevel", ""),
                "status": p.get("businessStatus", ""),
                "ptype": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
            }
    except Exception as e:
        print(f"  ! {type(e).__name__}: {str(e)[:80]}")
    cache[query] = out
    time.sleep(SLEEP)
    return out


def momentum_score(rating, reviews, price):
    """勢い（インパクト）スコア 0〜100。評価×集客規模×価格帯の合成。
    AS内の序列づけに使う暫定式。テストで効き目を見て調整する。"""
    import math
    r = float(rating or 0)
    n = int(reviews or 0)
    # 集客規模: 口コミ数の対数（0→0, 50→~28, 200→~40, 1000→~55上限）
    vol = min(55, 14 * math.log10(n + 1))
    # 評価: 3.0未満は0、3.0〜4.5を0〜30へ
    qual = max(0, min(30, (r - 3.0) / 1.5 * 30)) if r else 0
    # 価格帯: 高単価ほど転換インパクト大
    pscore = {"¥": 3, "¥¥": 8, "¥¥¥": 13, "¥¥¥¥": 15}.get(PRICE_MAP.get(price, ""), 0)
    return round(vol + qual + pscore)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--limit", type=int, default=300, help="先頭N件で小規模テスト")
    g.add_argument("--all", action="store_true", help="全件（本番・要課金確認）")
    args = ap.parse_args()

    if not API_KEY:
        print("❌ APIキーが見つかりません（_config/google_places_key.local）")
        sys.exit(1)

    targets = p04c.load_keizai_unmatched()
    if not args.all:
        targets = targets[: args.limit]
    print(f"エンリッチ対象: {len(targets)} 件（{'全件' if args.all else f'先頭{args.limit}件テスト'}）")

    cache = load_cache()
    session = requests.Session()
    import csv

    rows = []
    hit = miss = closed = 0
    new_calls = 0
    for k, rec in enumerate(targets, 1):
        q = f"{p04c.clean_name(rec['店名'])} {rec['市区町村']} {rec['都道府県']}"
        if q not in cache:
            new_calls += 1
        res = enrich_search(q, session, cache)
        if not res:
            miss += 1
            continue
        if res.get("status") == "CLOSED_PERMANENTLY":
            closed += 1
        hit += 1
        rows.append({
            "店名": rec["店名"],
            "市区町村": rec["市区町村"],
            "プラン": rec["プラン"],
            "評価": res.get("rating") or "",
            "口コミ数": res.get("reviews") or "",
            "価格帯": PRICE_MAP.get(res.get("price", ""), ""),
            "営業状況": res.get("status", ""),
            "業種": res.get("ptype", ""),
            "勢いスコア": momentum_score(res.get("rating"), res.get("reviews"), res.get("price")),
            "住所": res.get("addr", ""),
        })
        if k % 50 == 0:
            save_cache(cache)
            print(f"  {k}/{len(targets)} 取得{hit} 不一致{miss} 閉業{closed} 新規call{new_calls}")

    save_cache(cache)
    rows.sort(key=lambda r: -r["勢いスコア"])
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()) if rows else ["店名"])
        w.writeheader()
        w.writerows(rows)

    # サマリ
    rated = [r for r in rows if r["評価"]]
    priced = [r for r in rows if r["価格帯"]]
    print("=" * 56)
    print(f"出力: {OUT_CSV}")
    print(f"  取得 {hit} / 不一致 {miss} / 閉業 {closed} / 新規API呼び出し {new_calls}")
    print(f"  評価あり: {len(rated)} ({100*len(rated)//max(hit,1)}%)  "
          f"価格帯あり: {len(priced)} ({100*len(priced)//max(hit,1)}%)")
    print("--- 勢いスコア上位10（AS内の本命候補）---")
    for r in rows[:10]:
        print(f"  {r['勢いスコア']:3d}  ★{r['評価'] or '-'} 口{r['口コミ数'] or '-':>4} {r['価格帯'] or '-':4} "
              f"{r['プラン']:8} {r['店名'][:22]}")


if __name__ == "__main__":
    main()
