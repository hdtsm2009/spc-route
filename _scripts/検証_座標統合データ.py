# -*- coding: utf-8 -*-
"""Step1b: 座標統合データ(b)の検証。掲載との突合・GSI成功/失敗・距離QA。
GSI_Google距離m は既存比較列(納品データ由来)があるときだけ参考値として使う。新規API呼び出しなし。
"""
import os
import sys
import csv
import collections

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
B = os.path.join(BASE, "_output", "座標統合データ_20260602.csv")
SRC = os.path.join(ROOT, "_マスタデータ", "スポカフェ公式エクスポート_20260602.csv")
QA = os.path.join(BASE, "_output", "_検証_座標QA_距離150m以上.csv")


def main():
    b = list(csv.DictReader(open(B, encoding="utf-8-sig")))
    src = [r for r in csv.DictReader(open(SRC, encoding="utf-8-sig"))
           if (r.get("ステータス") or "").strip() == "掲載"]
    src_ids = {(r.get("店舗ID") or "").strip() for r in src}
    b_ids = {(r.get("スポカフェ店舗ID") or "").strip() for r in b}

    src_uniq = len(src_ids)
    by_src = collections.Counter(r.get("座標の出所", "") for r in b)
    gsi_ok = sum(1 for r in b if r.get("緯度"))
    fail = len(b) - gsi_ok
    missing = src_ids - b_ids  # 掲載なのに(b)に無い数値ID

    # 距離QA（既存比較列があるもののみ）
    far = []
    for r in b:
        d = r.get("GSI_Google距離m", "")
        if str(d).strip().isdigit() and int(d) >= 150:
            far.append(r)
    far.sort(key=lambda r: -int(r["GSI_Google距離m"]))
    with open(QA, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["スポカフェ店舗ID", "店名", "住所", "GSI_Google距離m"])
        for r in far:
            w.writerow([r.get("スポカフェ店舗ID"), r.get("店名"), r.get("住所"), r.get("GSI_Google距離m")])

    print(f"公式掲載ユニークID: {src_uniq}")
    print(f"(b)行数: {len(b)}  出所内訳: {dict(by_src)}")
    print(f"GSI座標あり: {gsi_ok} / 失敗(座標なし): {fail}")
    print(f"掲載なのに(b)に無いID（数値ID重複の先勝ちドロップ等）: {len(missing)}")
    print(f"旧Google比較で150m以上ズレ(参考QA): {len(far)} → {os.path.basename(QA)}")


if __name__ == "__main__":
    main()
