"""フェーズ07: スポカフェ掲載店のPOI誤マッチ座標を、新公式エクスポート由来のGSI座標で上書き。

背景: 旧パイプラインはスポカフェ掲載店の座標を Google店名検索(POI) で補完していたが、
      同名別店の誤マッチが多発（1km+ズレ109件・最大830km）。今回、番地付き新公式エクスポートを
      国土地理院でジオコーディングした座標統合データ(b)が得られたので、これで上書きする。

方式B（座標オーバーレイ）:
  - 対象: スポカフェ掲載店のうち **POI/補完由来**（ソースに'POI'or'補完'を含む）かつ **営業ランク≠除外**。
    スクレイプ名寄せ済み(食べログ/ダーツ等・精度詳細)と閉店/誤検出除外店は触らない。
  - 突合: 店名（正規化）で(b)とJOIN。同名複数は住所の市区町村で曖昧性解消、無理なら据え置き。
  - 上書き: 緯度/経度/住所(番地付き)/ジオコーディング精度=詳細(住所GSI)/geo_quality=A/ソース。
  - 勢いスコア・営業ランク・評価/口コミ・訪問記録(KV)は一切変更しない（今日の成果を保持）。

入力 : _output/統合店舗マスタ_v2.csv（上書き・_archiveにバックアップ）
       _output/座標統合データ_20260602.csv
出力 : 統合店舗マスタ_v2.csv 更新 → マスタJSONエクスポート.py で stores.json 再生成
"""
import os
import re
import csv
import sys
import shutil
import unicodedata

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
V2 = os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv")
GSI = os.path.join(BASE, "_output", "座標統合データ_20260602.csv")
BACKUP = os.path.join(BASE, "_output", "_archive", "統合店舗マスタ_v2_前フェーズ07.csv")


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\s　・,，。.\-_/|｜()（）'\"’]", "", s)


def main():
    if not os.path.exists(GSI):
        print(f"❌ GSI座標データが無い: {GSI}"); sys.exit(1)

    # (b) を 正規化店名 → [行] に
    gmap = {}
    gok = 0
    for r in csv.DictReader(open(GSI, encoding="utf-8-sig")):
        if not r.get("緯度") or r.get("座標の出所") == "失敗":
            continue
        gok += 1
        gmap.setdefault(norm(r["店名"]), []).append(r)
    print(f"(b)座標あり {gok} 件 / ユニーク店名 {len(gmap)}")

    rows = list(csv.DictReader(open(V2, encoding="utf-8-sig")))
    fieldnames = list(rows[0].keys())

    overlaid = ambiguous_skip = no_match = 0
    for r in rows:
        if r.get("スポカフェ掲載") != "○":
            continue
        src = str(r.get("ソース", ""))
        if ("POI" not in src) and ("補完" not in src):
            continue  # スクレイプ名寄せ済み（座標は信頼できる）は触らない
        if str(r.get("営業ランク", "")).strip() == "除外":
            continue  # 閉店/誤検出除外店は対象外
        cands = gmap.get(norm(r["店名"]))
        if not cands:
            no_match += 1
            continue
        if len(cands) > 1:
            # 同名複数 → 住所の文字重なりが最大の(b)行を選ぶ。決め手なければ据え置き
            cur = norm(r.get("住所", ""))
            best, score = None, 0
            for c in cands:
                ca = norm(c.get("住所", ""))
                ov = sum(1 for ch in set(ca) if ch in cur)
                if ov > score:
                    score, best = ov, c
            if not best or score < 2:
                ambiguous_skip += 1
                continue
            g = best
        else:
            g = cands[0]
        # 上書き
        addr = g.get("住所", "")
        if g.get("建物", "").strip():
            addr = (addr + " " + g["建物"]).strip()
        r["緯度"] = g["緯度"]
        r["経度"] = g["経度"]
        r["住所"] = addr
        r["ジオコーディング精度"] = "詳細(住所GSI)"
        r["geo_quality"] = "A"
        r["ソース"] = "スポカフェ公式(番地GSI)"
        overlaid += 1

    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    if not os.path.exists(BACKUP):
        shutil.copy2(V2, BACKUP)
    with open(V2, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    print("=" * 56)
    print(f"出力(上書き): {V2}  （バックアップ: _archive/統合店舗マスタ_v2_前フェーズ07.csv）")
    print(f"  GSI座標で上書き: {overlaid} 件")
    print(f"  同名複数で据え置き: {ambiguous_skip} 件 / 店名不一致で据え置き: {no_match} 件")


if __name__ == "__main__":
    main()
