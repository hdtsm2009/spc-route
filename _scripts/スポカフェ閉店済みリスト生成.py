"""スポカフェ掲載店の「閉店済みリスト」納品データ生成（多段検証 L1+L2+L3 反映版）。

入力:
  _output/_閉店検証_L1L2.csv  … L1(同名同市)+L2(Details)二重検証の結果（確信度 高/中/低）
  _data/l3_verdicts.json      … L3=Web独立確認の人手判定（重要店）

出力: _納品_スポカフェ閉店済みリスト_<日付>/
  閉店確定_削除推奨_<日付>.csv … 削除を推奨できる確度の店
  要確認_<日付>.csv            … L3で矛盾/裏取り弱・現地確認が要る店
  検証で除外_誤検出_<日付>.csv … L1で別店誤検出と判明（閉店フラグを外すべき・参考）
  README_閉店リスト説明.md

確信度の最終ラベル:
  三重(Web確認済)  = L1L2高 ＋ L3=閉店確定
  二重(要現地推奨) = L1L2高 ＋ L3対象外（フリー・低口コミ）
  要確認           = L3=営業中疑い（矛盾） / L1L2中
  誤検出           = L1L2低（同名同市の一致なし）
"""
import os
import csv
import sys
import json
import glob
import datetime
import importlib.util

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
L1L2_CSV = os.path.join(BASE, "_output", "_閉店検証_L1L2.csv")
L3_JSON = os.path.join(BASE, "_data", "l3_verdicts.json")
REGEO_CSV = os.path.join(BASE, "_output", "_誤検出再ジオコーディング_結果.csv")

_spec = importlib.util.spec_from_file_location(
    "p04c", os.path.join(BASE, "_scripts", "フェーズ04c_店名POIジオコーディング.py"))
p04c = importlib.util.module_from_spec(_spec)
sys.path.insert(0, os.path.join(BASE, "_scripts"))
_spec.loader.exec_module(p04c)


def main():
    if not os.path.exists(L1L2_CSV):
        print(f"❌ {L1L2_CSV} が無い。先に スポカフェ閉店検証_L1L2.py を実行。")
        sys.exit(1)
    rows = list(csv.DictReader(open(L1L2_CSV, encoding="utf-8-sig")))
    l3 = json.load(open(L3_JSON, encoding="utf-8"))

    confirmed, review, falsepos = [], [], []
    for r in rows:
        v = l3.get(r["店名"], {})
        l3v = v.get("verdict", "")
        base = {
            "店舗ID": r["店舗ID"], "店名": r["店名"], "プラン": r["プラン"],
            "都道府県": r["都道府県"], "市区町村": r["市区町村"],
            "評価": r["評価"], "口コミ数": r["口コミ数"],
        }
        if r["確信度"] == "低":
            falsepos.append({**base, "理由": r["判定根拠"]})
            continue
        # 確信度 高/中
        if l3v == "閉店確定":
            confirmed.append({**base, "検証": "三重(Web確認済)",
                              "閉店時期": v.get("date", "不明"),
                              "Web根拠": v.get("note", ""), "出典URL": v.get("src", "")})
        elif l3v == "営業中疑い":
            review.append({**base, "検証": "要確認(L3で営業中疑い)",
                           "備考": v.get("note", ""), "出典URL": v.get("src", "")})
        elif r["確信度"] == "高":
            confirmed.append({**base, "検証": "二重(要現地確認推奨)",
                              "閉店時期": "", "Web根拠": "", "出典URL": ""})
        else:  # 中
            review.append({**base, "検証": "要確認(裏取り1本)", "備考": r["判定根拠"], "出典URL": ""})

    # 並び: 有料プラン優先→口コミ多い順
    def sk(r):
        paid = 0 if (r["プラン"] and r["プラン"] != "フリー") else 1
        n = int(r["口コミ数"]) if str(r["口コミ数"]).isdigit() else 0
        return (paid, -n)
    for L in (confirmed, review, falsepos):
        L.sort(key=sk)

    today = datetime.date.today().strftime("%Y%m%d")
    outdir = os.path.join(BASE, f"_納品_スポカフェ閉店済みリスト_{today}")
    os.makedirs(outdir, exist_ok=True)

    def write(name, rows_, cols):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8-sig", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); w.writerows(rows_)
        return path

    cc = ["店舗ID", "店名", "プラン", "都道府県", "市区町村", "検証",
          "閉店時期", "Web根拠", "出典URL", "評価", "口コミ数"]
    rc = ["店舗ID", "店名", "プラン", "都道府県", "市区町村", "検証", "備考", "出典URL", "評価", "口コミ数"]
    fc = ["店舗ID", "店名", "プラン", "都道府県", "市区町村", "理由", "評価", "口コミ数"]
    write(f"閉店確定_削除推奨_{today}.csv", confirmed, cc)
    write(f"要確認_{today}.csv", review, rc)
    write(f"検証で除外_誤検出_{today}.csv", falsepos, fc)

    # 所在不明（番地なし＋POI誤検出＋再ジオコーディングでもGoogle上に見つからない掲載店）
    unfound = []
    if os.path.exists(REGEO_CSV):
        for r in csv.DictReader(open(REGEO_CSV, encoding="utf-8-sig")):
            if r["確信度"] == "不可":
                unfound.append({"店舗ID": r["店舗ID"], "店名": r["店名"], "プラン": r["プラン"],
                                "都道府県": r["都道府県"], "市区町村": r["市区町村"],
                                "状態": "Googleでも店名で所在特定できず（実在・営業を要確認）"})
        unfound.sort(key=lambda r: (0 if (r["プラン"] and r["プラン"] != "フリー") else 1, r["市区町村"]))
        write(f"所在不明_要実在確認_{today}.csv", unfound,
              ["店舗ID", "店名", "プラン", "都道府県", "市区町村", "状態"])

    triple = sum(1 for r in confirmed if r["検証"].startswith("三重"))
    double = len(confirmed) - triple
    paid_c = sum(1 for r in confirmed if r["プラン"] and r["プラン"] != "フリー")

    with open(os.path.join(outdir, "README_閉店リスト説明.md"), "w", encoding="utf-8") as fp:
        fp.write(f"""# スポカフェ掲載店 閉店済みリスト（{today}・多段検証版）

スポカフェ掲載店（番地なしでPOI補完した {len(rows)+0}件規模の母集団）について、
Google Places の閉業フラグを起点に **3段階で検証**した結果です。マスタ掲載解除の検討にご利用ください。

## 検証フロー
1. **L1 同名・同市チェック**: Places再検索で「店名一致」かつ「同一市区町村」の結果のみ採用。
   同名の別店（海外・別市の同名店）を除去。→ ここで多数を誤検出として除外。
2. **L2 Place Details 再確認**: place_id から権威エンドポイントで businessStatus を再取得。
3. **L3 Web独立確認**（有料プラン・口コミ多数の重要店のみ）: 「店名＋市区町村＋閉店」をWeb検索し、
   食べログ「閉店」表記・公式サイト/SNS・ニュースで裏取り。Googleと独立した第三者ソースで確認。

## ファイルと中身
- `閉店確定_削除推奨_{today}.csv` … **{len(confirmed)}件**（うち有料プラン {paid_c}件）
  - `検証=三重(Web確認済)` {triple}件: L1+L2＋Web独立ソースで閉店裏取り済（出典URL付き）。**削除して概ね安全**。
  - `検証=二重(要現地確認推奨)` {double}件: L1+L2は通過したがWeb未確認（フリー・低口コミ）。**現地/電話で最終確認推奨**。
- `要確認_{today}.csv` … {len(review)}件: L3で**営業中の形跡**があり矛盾（同名別支店の疑い等）。**削除しない**。
- `検証で除外_誤検出_{today}.csv` … {len(falsepos)}件: L1で別店誤検出と判明。元の閉業フラグは無関係な店のもの。**参考（削除対象外）**。
- `所在不明_要実在確認_{today}.csv` … {len(unfound)}件: 番地が無くPOIも誤検出、**再検索しても店名でGoogleに見つからない**掲載店。
  閉店して地図から消えた／改名／元々ネット未掲載の可能性。**実在・営業の確認を推奨**（掲載しているのにどこにも出ないのは不自然）。

## 重要な注意
- 母集団は「番地が無くPOI（店名＋市区町村）で補完した掲載店」。番地ありで名寄せ済みの掲載店は本リスト対象外。
- `三重`でも最終判断は運用側で。特に有料プラン店は掲載解除前に必ず一次情報（電話/公式）で確認してください。
- `出典URL` は確認時点（{today}）のもの。

## 推奨運用
1. `閉店確定_削除推奨` の **三重(Web確認済)** から掲載解除を進める（出典URLで根拠確認）
2. **二重** は現地/電話で生存確認してから解除
3. `要確認` は同名別店の可能性が高いので解除しない（むしろ正しい店の再ジオコーディング候補）
""")

    print("=" * 56)
    print(f"出力: {outdir}")
    print(f"  閉店確定_削除推奨: {len(confirmed)}（三重 {triple} / 二重 {double} / 有料 {paid_c}）")
    print(f"  要確認: {len(review)}")
    print(f"  検証で除外(誤検出): {len(falsepos)}")


if __name__ == "__main__":
    main()
