"""フェーズ06: AS店に勢い(評価/口コミ/価格)を還元＋勢いスコア列追加＋閉店/誤検出を除外。

入力:
  _output/統合店舗マスタ_v2.csv        … フェーズ05出力
  _data/places_enrich_cache.json       … 04d取得の評価/口コミ/価格/営業状況
  _output/_閉店検証_L1L2.csv           … 多段検証の確信度
  _data/l3_verdicts.json               … L3=Web独立確認
出力:
  _output/統合店舗マスタ_v2.csv        … 上書き（_archiveにバックアップ）
    追加/更新列: 評価/口コミ数/予算(空欄のみ補完), 勢いスコア, 営業ランク(閉店/誤検出→除外), 除外理由

判定の取り込み（スポカフェ閉店済みリスト生成.py と同じ三重検証ロジック）:
  閉店確定(三重/二重) → ランク除外「閉店確定」
  誤検出(L1低)        → ランク除外「POI名一致弱(座標要再確認)」※stores.jsonには残し復元可
  営業中疑い(L3矛盾)  → 除外しない（要確認のまま候補に残す）
"""
import os
import csv
import sys
import json
import math
import shutil
import importlib.util

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
V2 = os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv")
ENRICH = os.path.join(BASE, "_data", "places_enrich_cache.json")
L1L2 = os.path.join(BASE, "_output", "_閉店検証_L1L2.csv")
L3J = os.path.join(BASE, "_data", "l3_verdicts.json")
REGEO = os.path.join(BASE, "_output", "_誤検出再ジオコーディング_結果.csv")
V2_BACKUP = os.path.join(BASE, "_output", "_archive", "統合店舗マスタ_v2_前フェーズ06.csv")

_spec = importlib.util.spec_from_file_location(
    "p04c", os.path.join(BASE, "_scripts", "フェーズ04c_店名POIジオコーディング.py"))
p04c = importlib.util.module_from_spec(_spec)
sys.path.insert(0, os.path.join(BASE, "_scripts"))
_spec.loader.exec_module(p04c)

PRICE_MAP = {"PRICE_LEVEL_FREE": "¥", "PRICE_LEVEL_INEXPENSIVE": "¥",
             "PRICE_LEVEL_MODERATE": "¥¥", "PRICE_LEVEL_EXPENSIVE": "¥¥¥",
             "PRICE_LEVEL_VERY_EXPENSIVE": "¥¥¥¥"}


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def momentum(rating, reviews, price_sym):
    r = _f(rating)
    n = int(_f(reviews))
    vol = min(55, 14 * math.log10(n + 1)) if n else 0
    qual = max(0, min(30, (r - 3.0) / 1.5 * 30)) if r else 0
    pscore = {"¥": 3, "¥¥": 8, "¥¥¥": 13, "¥¥¥¥": 15}.get(price_sym, 0)
    return round(vol + qual + pscore)


def build_enrich_map():
    """store_id → {rating,reviews,price_sym}（within_city通過分のみ採用）。"""
    cache = json.load(open(ENRICH, encoding="utf-8"))
    m = {}
    for rec in p04c.load_keizai_unmatched():
        pref, city = rec["都道府県"], rec["市区町村"]
        q = f"{p04c.clean_name(rec['店名'])} {city} {pref}"
        v = cache.get(q)
        if not v:
            continue
        if not p04c.within_city(v.get("addr", ""), pref, city):
            continue  # 別市/海外の誤一致は採用しない
        sid = p04c.store_id(rec["店名"], pref, city)
        m[sid] = {
            "rating": v.get("rating"),
            "reviews": v.get("reviews"),
            "price": PRICE_MAP.get(v.get("price", ""), ""),
        }
    return m


def build_exclusion_sets():
    """店舗ID(SPMハッシュ) → 除外理由。閉店確定/誤検出を分ける。"""
    rows = list(csv.DictReader(open(L1L2, encoding="utf-8-sig")))
    l3 = json.load(open(L3J, encoding="utf-8"))
    closed, falsepos = {}, {}
    for r in rows:
        sid = p04c.store_id(r["店名"], r["都道府県"], r["市区町村"])
        l3v = l3.get(r["店名"], {}).get("verdict", "")
        if r["確信度"] == "低":
            falsepos[sid] = "POI名一致弱(座標要再確認)"
        elif l3v == "営業中疑い":
            continue  # 要確認 → 除外しない
        elif r["確信度"] in ("高", "中"):
            tag = "閉店確定(Web確認済)" if l3v == "閉店確定" else "閉店確定(要現地確認)"
            closed[sid] = tag
    return closed, falsepos


def build_regeo_override():
    """誤検出のうち再ジオコーディングで店名+市/県一致した店を復活。
    店舗ID(SPM) → {lat,lng,rating,reviews,closed}。確信度『高』かつ営業中のみ採用。"""
    if not os.path.exists(REGEO):
        return {}
    m = {}
    for r in csv.DictReader(open(REGEO, encoding="utf-8-sig")):
        if r["確信度"] != "高" or not r["新緯度"]:
            continue  # 高=店名+市一致のみ採用（中は別支店誤マッチ多く不採用）
        m[r["店舗ID"]] = {
            "lat": r["新緯度"], "lng": r["新経度"],
            "rating": r.get("評価", ""), "reviews": r.get("口コミ数", ""),
            "closed": r["営業状況"] in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"),
        }
    return m


def main():
    for p in (ENRICH, L1L2, L3J):
        if not os.path.exists(p):
            print(f"❌ 入力が無い: {p}"); sys.exit(1)
    # 再実行を冪等にするため、フェーズ05のクリーン出力(=06前バックアップ)があればそれを入力に使う
    src = V2_BACKUP if os.path.exists(V2_BACKUP) else V2
    print(f"入力: {src}")
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    fieldnames = list(rows[0].keys())
    if "勢いスコア" not in fieldnames:
        # 営業スコアの後ろに挿入
        i = fieldnames.index("営業スコア") + 1 if "営業スコア" in fieldnames else len(fieldnames)
        fieldnames = fieldnames[:i] + ["勢いスコア"] + fieldnames[i:]

    enrich = build_enrich_map()
    closed, falsepos = build_exclusion_sets()
    regeo = build_regeo_override()

    n_fill = n_mom = n_closed = n_fp = n_revived = 0
    for r in rows:
        sid = r.get("店舗ID", "")
        # 0) 再ジオコーディング復活（誤検出だが店名+市一致で正座標が取れた店）
        rg = regeo.get(sid)
        if rg:
            if rg["closed"]:
                closed[sid] = "閉店確定(再取得で判明)"      # 復活せず閉店扱い
            else:
                falsepos.pop(sid, None)                      # 誤検出指定を解除＝復活
                r["緯度"], r["経度"] = rg["lat"], rg["lng"]
                r["ジオコーディング精度"] = "詳細(POI再取得)"
                r["geo_quality"] = "A"
                if rg["rating"]:
                    r["評価"] = rg["rating"]
                if rg["reviews"]:
                    r["口コミ数"] = rg["reviews"]
                n_revived += 1
        # 1) 評価/口コミ/予算 を空欄のみ補完（within_city通過のAS）
        e = enrich.get(sid)
        if e:
            if not str(r.get("評価", "")).strip() and e["rating"]:
                r["評価"] = e["rating"]; n_fill += 1
            if not str(r.get("口コミ数", "")).strip() and e["reviews"]:
                r["口コミ数"] = e["reviews"]
            if not str(r.get("予算", "")).strip() and e["price"]:
                r["予算"] = e["price"]
        # 2) 勢いスコア（評価/口コミから・全ランク共通の2次ソートキー）
        price_sym = (e["price"] if e else "") or (r.get("予算", "") if r.get("予算", "").startswith("¥") else "")
        mom = momentum(r.get("評価"), r.get("口コミ数"), price_sym)
        r["勢いスコア"] = mom
        if mom:
            n_mom += 1
        # 3) 閉店/誤検出を除外
        if sid in closed:
            r["営業ランク"] = "除外"
            r["除外理由"] = (r.get("除外理由", "") + " / " if r.get("除外理由") else "") + closed[sid]
            n_closed += 1
        elif sid in falsepos:
            r["営業ランク"] = "除外"
            r["除外理由"] = (r.get("除外理由", "") + " / " if r.get("除外理由") else "") + falsepos[sid]
            n_fp += 1

    # フェーズ05クリーン出力のバックアップは初回のみ作成（再実行で壊さない）
    os.makedirs(os.path.dirname(V2_BACKUP), exist_ok=True)
    if not os.path.exists(V2_BACKUP):
        shutil.copy2(V2, V2_BACKUP)
    with open(V2, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    print("=" * 56)
    print(f"出力(上書き): {V2}  （バックアップ: _output/_archive/）")
    print(f"  評価補完: {n_fill} 件 / 勢いスコア付与: {n_mom} 件")
    print(f"  再ジオコーディング復活: {n_revived} 件")
    print(f"  閉店除外: {n_closed} 件 / 誤検出除外: {n_fp} 件")
    # AS勢い上位を確認
    asr = [r for r in rows if r.get("営業ランク") == "AS"]
    asr.sort(key=lambda r: -int(r.get("勢いスコア") or 0))
    print(f"  AS残: {len(asr)} 件。勢い上位5:")
    for r in asr[:5]:
        print(f"    勢{r['勢いスコア']:3} ★{r.get('評価') or '-'} 口{r.get('口コミ数') or '-':>5} {r.get('店名','')[:24]}")


if __name__ == "__main__":
    main()
