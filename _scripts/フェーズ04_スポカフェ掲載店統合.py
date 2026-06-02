# -*- coding: utf-8 -*-
"""フェーズ04: スクレイプ未マッチのスポカフェ掲載店を、番地付き住所のGSI座標で統合に追加。
（旧 04b 地名概算 / 04c POI店名検索 / 07 GSI上書き を置換。POIは一切使わない）

突合（スポカフェ掲載店 → スクレイプ側に既出か）は段階的・**店名単体禁止**：
  ①電話一致 ②(店名+都道府県+市区町村)一致 ③(店名+住所市区町村)一致 → 既出＝スキップ
  上記で当たらない＝未マッチ → 新規行として追加（同名別店を既出扱いして取りこぼさない）

座標は (b)_output/座標統合データ_20260602.csv を **スポカフェ数値IDで正確JOIN**。
store_id: 電話一意→assign_id(電話S-ID統一)、電話なし/電話衝突→"SPC"+数値ID。
ファンスタ: 電話一致(一意)で ○ 付与。

入力: _output/統合店舗マスタ_geocoded.csv, _マスタデータ/スポカフェ公式エクスポート_20260602.csv,
      _output/座標統合データ_20260602.csv, _マスタデータ/ファンスタ収集データ_*.xlsx
出力: _output/補完_スポカフェ掲載店_GSI.csv（geocoded.csv と同じ列）, _output/_新04_取込サマリ.txt
"""
import os
import sys
import csv
import glob
import json
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import norm_name, norm_phone, extract_pref_city  # noqa
import 統合マスタ構築 as M  # load_spocafe_master/load_fansta_master/assign_id/COLUMNS 等  # noqa

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
MASTER = os.path.join(ROOT, "_マスタデータ")
GEOCODED = os.path.join(BASE, "_output", "統合店舗マスタ_geocoded.csv")
SPOCAFE = sorted(glob.glob(os.path.join(MASTER, "スポカフェ公式エクスポート_*.csv")))[-1]
GSI_B = os.path.join(BASE, "_output", "座標統合データ_20260602.csv")
OUT = os.path.join(BASE, "_output", "補完_スポカフェ掲載店_GSI.csv")
SUMMARY = os.path.join(BASE, "_output", "_新04_取込サマリ.txt")


def main():
    # スクレイプ側（既存統合マスタ）の列構成
    geo = list(csv.DictReader(open(GEOCODED, encoding="utf-8-sig")))
    fieldnames = list(geo[0].keys())
    # 統合マスタ構築が名寄せ済みとした スポカフェ数値ID（重複防止の正・突合方向の非対称を排除）
    matched_ids = set(json.load(open(
        os.path.join(BASE, "_output", "_スポカフェ_matched_ids.json"), encoding="utf-8")))

    spo = M.load_spocafe_master(SPOCAFE)
    fansta_path = sorted(glob.glob(os.path.join(MASTER, "ファンスタ収集データ_*.xlsx")))[-1]
    fansta = M.load_fansta_master(fansta_path)
    fansta_phones = {f["電話"] for f in fansta.values() if f.get("電話")}

    # (b) 座標: スポカフェ数値ID → {lat,lng,精度,住所,建物}
    bmap = {}
    for r in csv.DictReader(open(GSI_B, encoding="utf-8-sig")):
        if not r.get("緯度"):
            continue
        bmap[(r.get("スポカフェ店舗ID") or "").strip()] = r

    id_map = M.load_id_map()
    prev_ids = len(id_map)

    out_rows = []
    n_matched = n_added = n_fail = n_spc = 0
    for inf in spo["all"]:
        if inf["数値ID"] in matched_ids:   # 統合マスタ構築で名寄せ済み＝重複追加しない
            n_matched += 1
            continue
        b = bmap.get(inf["数値ID"])
        if not b:
            n_fail += 1  # GSI座標なし → 追加しない（NG間引き相当）
            continue
        # store_id: 電話一意→電話S-ID統一 / それ以外→SPC+数値ID
        if inf["電話"] and inf["電話"] not in spo["phone_ambiguous"]:
            sid = M.assign_id(inf["電話"], norm_name(inf["店名"]), id_map)
        else:
            sid = "SPC" + inf["数値ID"]
            n_spc += 1
        addr = b.get("住所", "") + ((" " + b["建物"]) if b.get("建物") else "")
        prec = "詳細(住所GSI)" if b.get("精度") == "詳細" else "町名(住所GSI)"
        in_fansta = "○" if (inf["電話"] and inf["電話"] in fansta_phones) else ""
        row = {fn: "" for fn in fieldnames}
        row.update({
            "店舗ID": sid, "店名": inf["店名"], "電話番号": inf["電話"],
            "住所": addr.strip(), "営業時間": inf.get("営業時間", ""),
            "ソース": "スポカフェ公式(番地GSI)",
            "スポカフェ掲載": "○", "スポカフェプラン": inf["プラン"],
            "ファンスタ掲載": in_fansta, "営業ターゲット": "★",
            "緯度": b.get("緯度", ""), "経度": b.get("経度", ""),
            "ジオコーディング精度": prec,
            "名寄せ_電話キー": inf["電話"], "名寄せ_店名キー": norm_name(inf["店名"]),
        })
        out_rows.append(row)
        n_added += 1

    M.save_id_map(id_map)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    listed = len(spo["all"])
    lines = [
        "=== 新04 スポカフェ掲載店統合 取込サマリ ===",
        f"公式掲載(ユニーク基準): {listed}",
        f"  scrapeマッチ(既出・スキップ): {n_matched}",
        f"  新規追加(GSI座標あり):       {n_added}（うちSPC採番 {n_spc} / 電話S-ID {n_added - n_spc}）",
        f"  座標失敗(GSI無し・除外):     {n_fail}",
        f"  → 内訳合計: {n_matched + n_added + n_fail} （= 公式掲載 と一致すべき）",
        f"新規ID発行: {len(id_map) - prev_ids}件",
        f"ファンスタ○付与(電話一致): {sum(1 for r in out_rows if r['ファンスタ掲載'])}",
        f"出力: {OUT}",
    ]
    report = "\n".join(lines)
    open(SUMMARY, "w", encoding="utf-8").write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
