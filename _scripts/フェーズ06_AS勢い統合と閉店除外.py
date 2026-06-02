"""フェーズ06: AS店に勢い(評価/口コミ/価格)を還元＋勢いスコア列追加＋閉店/誤検出を除外。

★正攻法移行版（store_id非依存・正規化店名キー）:
  正本ソース差し替えで store_id 体系が変わるため、勢い/閉店/誤検出を **正規化店名キー** で引き継ぐ。
  - 勢い(enrich): 店名キーで適用（軽傷。同名衝突は口コミ最大を採用）。
  - 閉店/誤検出除外: **一意解決時のみ適用**（閉店セット内で一意 かつ v2内で一意）。
    非一意は適用見送り＋ _ambiguous_phase06.csv（候補からは落とさない＝誤爆防止）。
  - 公式GSI座標(ソース=スポカフェ公式(番地GSI))には再ジオ上書きを適用しない（座標優先=公式GSI）。

2モード:
  python フェーズ06...py --dry-run   # 移行レポートのみ出力（v2は書き換えない）
  python フェーズ06...py             # 本適用（v2上書き・_archiveにバックアップ）

入力: _output/統合店舗マスタ_v2.csv, _data/places_enrich_cache.json,
      _output/_閉店検証_L1L2.csv, _data/l3_verdicts.json
出力: v2上書き / _output/_phase06_移行レポート.csv / _output/_ambiguous_phase06.csv
"""
import os
import csv
import sys
import json
import math
import shutil
import argparse
import collections
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import norm_name  # noqa

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
V2 = os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv")
ENRICH = os.path.join(BASE, "_data", "places_enrich_cache.json")
L1L2 = os.path.join(BASE, "_output", "_閉店検証_L1L2.csv")
L3J = os.path.join(BASE, "_data", "l3_verdicts.json")
V2_BACKUP = os.path.join(BASE, "_output", "_archive", "統合店舗マスタ_v2_前フェーズ06.csv")
REPORT = os.path.join(BASE, "_output", "_phase06_移行レポート.csv")
AMBIG = os.path.join(BASE, "_output", "_ambiguous_phase06.csv")

# clean_name / load_keizai_unmatched / within_city は enrichキャッシュのキー再生成に流用
_spec = importlib.util.spec_from_file_location(
    "p04c", os.path.join(BASE, "_scripts", "フェーズ04c_店名POIジオコーディング.py"))
p04c = importlib.util.module_from_spec(_spec)
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


def build_enrich_by_name():
    """正規化店名 → {rating,reviews,price}。enrichキャッシュ(クエリ=店名+市区町村+都道府県)を
    旧マスタの店名で再構成して引く（within_city通過のみ）。同名衝突は口コミ最大を採用（軽傷）。"""
    cache = json.load(open(ENRICH, encoding="utf-8"))
    m = {}
    for rec in p04c.load_keizai_unmatched():
        pref, city = rec["都道府県"], rec["市区町村"]
        q = f"{p04c.clean_name(rec['店名'])} {city} {pref}"
        v = cache.get(q)
        if not v or not p04c.within_city(v.get("addr", ""), pref, city):
            continue
        nn = norm_name(rec["店名"])
        if not nn:
            continue
        cand = {"rating": v.get("rating"), "reviews": v.get("reviews"),
                "price": PRICE_MAP.get(v.get("price", ""), "")}
        prev = m.get(nn)
        if prev is None or _f(cand["reviews"]) > _f(prev["reviews"]):
            m[nn] = cand
    return m


def build_exclusion_by_name():
    """正規化店名 → (tag, kind)。閉店確定/誤検出。閉店セット内で同名複数の店名は
    ambiguous として除外（_ambiguous_phase06.csv へ・適用しない）。"""
    rows = list(csv.DictReader(open(L1L2, encoding="utf-8-sig")))
    l3 = json.load(open(L3J, encoding="utf-8"))
    by_name = collections.defaultdict(list)  # nn -> list of (tag, kind, 店名, 都道府県, 市区町村)
    for r in rows:
        nn = norm_name(r["店名"])
        if not nn:
            continue
        l3v = l3.get(r["店名"], {}).get("verdict", "")
        if r["確信度"] == "低":
            by_name[nn].append(("POI名一致弱(座標要再確認)", "falsepos", r["店名"], r["都道府県"], r["市区町村"]))
        elif l3v == "営業中疑い":
            continue  # 要確認 → 除外しない
        elif r["確信度"] in ("高", "中"):
            tag = "閉店確定(Web確認済)" if l3v == "閉店確定" else "閉店確定(要現地確認)"
            by_name[nn].append((tag, "closed", r["店名"], r["都道府県"], r["市区町村"]))
    uniq, ambiguous = {}, []
    for nn, lst in by_name.items():
        kinds = {x[1] for x in lst}
        # 同名で複数エントリ（別店混在の恐れ）or closed/falsepos混在 → 適用見送り
        if len(lst) > 1 or len(kinds) > 1:
            ambiguous.append((nn, lst))
            continue
        tag, kind = lst[0][0], lst[0][1]
        uniq[nn] = (tag, kind)
    return uniq, ambiguous


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="移行レポートのみ・v2は書き換えない")
    args = ap.parse_args()

    for p in (V2, ENRICH, L1L2, L3J):
        if not os.path.exists(p):
            print(f"❌ 入力が無い: {p}"); sys.exit(1)
    # 冪等化: フェーズ05クリーン出力(=06前バックアップ)があればそれを入力に
    src = V2_BACKUP if os.path.exists(V2_BACKUP) else V2
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    fieldnames = list(rows[0].keys())
    if "勢いスコア" not in fieldnames:
        i = fieldnames.index("営業スコア") + 1 if "営業スコア" in fieldnames else len(fieldnames)
        fieldnames = fieldnames[:i] + ["勢いスコア"] + fieldnames[i:]

    enrich = build_enrich_by_name()
    excl, ambiguous = build_exclusion_by_name()

    # v2内の正規化店名出現数（一意判定に使用）
    name_count = collections.Counter(norm_name(r.get("店名", "")) for r in rows)

    n_fill = n_mom = n_closed = n_fp = n_excl_ambig = 0
    ambig_log = []
    for r in rows:
        nn = norm_name(r.get("店名", ""))
        # 1) 勢い: 店名キーで補完（空欄のみ）
        e = enrich.get(nn)
        if e:
            if not str(r.get("評価", "")).strip() and e["rating"]:
                r["評価"] = e["rating"]; n_fill += 1
            if not str(r.get("口コミ数", "")).strip() and e["reviews"]:
                r["口コミ数"] = e["reviews"]
            if not str(r.get("予算", "")).strip() and e["price"]:
                r["予算"] = e["price"]
        # 2) 勢いスコア
        price_sym = (e["price"] if e else "") or (r.get("予算", "") if str(r.get("予算", "")).startswith("¥") else "")
        mom = momentum(r.get("評価"), r.get("口コミ数"), price_sym)
        r["勢いスコア"] = mom
        if mom:
            n_mom += 1
        # 3) 閉店/誤検出除外（一意解決時のみ：閉店セット一意 かつ v2内で同名一意）
        if nn and nn in excl:
            if name_count[nn] == 1:
                tag, kind = excl[nn]
                r["営業ランク"] = "除外"
                r["除外理由"] = (r.get("除外理由", "") + " / " if r.get("除外理由") else "") + tag
                if kind == "closed":
                    n_closed += 1
                else:
                    n_fp += 1
            else:
                n_excl_ambig += 1
                ambig_log.append((nn, r.get("店名", ""), "v2内同名複数で除外見送り"))

    for nn, lst in ambiguous:
        ambig_log.append((nn, " | ".join(x[2] for x in lst), "閉店セット内で同名複数/種別混在"))

    # レポート出力
    with open(REPORT, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["指標", "件数"])
        w.writerow(["勢い補完(評価)", n_fill])
        w.writerow(["勢いスコア>0", n_mom])
        w.writerow(["閉店除外(適用)", n_closed])
        w.writerow(["誤検出除外(適用)", n_fp])
        w.writerow(["除外見送り(v2内同名複数)", n_excl_ambig])
        w.writerow(["閉店セット内ambiguous店名", len(ambiguous)])
    with open(AMBIG, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["norm_name", "店名", "理由"])
        w.writerows(ambig_log)

    print("=" * 56)
    print(f"{'[DRY-RUN] ' if args.dry_run else ''}フェーズ06 移行レポート")
    print(f"  勢い補完: {n_fill} / 勢いスコア>0: {n_mom}")
    print(f"  閉店除外(適用): {n_closed} / 誤検出除外(適用): {n_fp}")
    print(f"  除外見送り(v2内同名複数): {n_excl_ambig} / 閉店セット内ambiguous: {len(ambiguous)}")
    print(f"  レポート: {REPORT} / ambiguous: {AMBIG}")
    asr = [r for r in rows if r.get("営業ランク") == "AS"]
    asr.sort(key=lambda r: -int(r.get("勢いスコア") or 0))
    print(f"  AS残: {len(asr)} 件。勢い上位5:")
    for r in asr[:5]:
        print(f"    勢{r['勢いスコア']:3} ★{r.get('評価') or '-'} 口{r.get('口コミ数') or '-':>5} {r.get('店名','')[:24]}")

    if args.dry_run:
        print(">>> DRY-RUN: v2は書き換えていません。問題なければ --dry-run 無しで本適用してください。")
        return

    os.makedirs(os.path.dirname(V2_BACKUP), exist_ok=True)
    if not os.path.exists(V2_BACKUP):
        shutil.copy2(V2, V2_BACKUP)
    with open(V2, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print(f"✅ 本適用: {V2} を上書き（バックアップ: _archive/）")


if __name__ == "__main__":
    main()
