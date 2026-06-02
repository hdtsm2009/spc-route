# -*- coding: utf-8 -*-
"""スポカフェ正本ソース（新公式エクスポート）の取込パイプラインを順に一括実行する。

定期運用: 新しい公式エクスポートを `_マスタデータ/スポカフェ公式エクスポート_<日付>.csv` として置き、
          本スクリプトを実行するだけ（各スクリプトは最新ファイルを自動選択）。POIは使わない。

安全運用: 既定はフェーズ06を **dry-run まで** で停止（移行レポートを確認する）。
          問題なければ `--apply` で フェーズ06本適用＋stores.json再生成まで通す。

  python _scripts/パイプライン全実行.py            # 検証＋dry-runまで
  python _scripts/パイプライン全実行.py --apply     # 本適用＋エクスポートまで
"""
import os
import sys
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(BASE, "_scripts")

# (説明, スクリプト, 追加引数)
STEPS = [
    ("0a preflight検証",        "検証_スポカフェ公式CSV.py", []),
    ("1  座標統合データ生成",    "座標統合データ生成.py", []),
    ("1b 座標統合データ検証",    "検証_座標統合データ.py", []),
    ("2  統合マスタ構築",        "統合マスタ構築.py", []),
    ("3  ジオコーディング",      "ジオコーディング.py", []),
    ("4  スポカフェ掲載店統合",  "フェーズ04_スポカフェ掲載店統合.py", []),
    ("5  品質強化",              "フェーズ05_品質強化.py", []),
    ("6  勢い/閉店 dry-run",     "フェーズ06_AS勢い統合と閉店除外.py", ["--dry-run"]),
]
APPLY_STEPS = [
    ("6  勢い/閉店 本適用",      "フェーズ06_AS勢い統合と閉店除外.py", []),
    ("7  マスタJSONエクスポート", "マスタJSONエクスポート.py", []),
]


def run(desc, script, args):
    print("\n" + "=" * 64)
    print(f"▶ {desc}  ({script} {' '.join(args)})")
    print("=" * 64)
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, os.path.join(S, script), *args], cwd=BASE, env=env)
    if r.returncode != 0:
        print(f"\n❌ 失敗: {script}（exit {r.returncode}）。ここで中断します。")
        sys.exit(r.returncode)


def main():
    apply = "--apply" in sys.argv
    for desc, script, args in STEPS:
        run(desc, script, args)
    if not apply:
        print("\n>>> dry-runまで完了。_output/_phase06_移行レポート.csv を確認し、"
              "問題なければ  python _scripts/パイプライン全実行.py --apply  で本適用してください。")
        return
    for desc, script, args in APPLY_STEPS:
        run(desc, script, args)
    print("\n✅ 全工程完了。git add api/ && commit && push でデプロイ（フェーズ07は実行しないこと）。")


if __name__ == "__main__":
    main()
