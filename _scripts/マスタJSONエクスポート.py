"""マスタJSONエクスポート。

統合店舗マスタ_v2.csv → netlify/functions/generate_plan/stores.json
設定.json            → netlify/functions/generate_plan/config.json

Netlify Functionのデプロイ前、またはマスタデータ更新後に実行してください。
"""
import os
import csv
import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
OUT_DIR = os.path.join(BASE, "netlify", "functions", "generate_plan")

# 出力に含めるフィールド（Netlify Functionで使用するもののみ）
KEEP_FIELDS = [
    "店舗ID", "店名", "住所", "緯度", "経度",
    "業態ジャンル", "営業ランク", "営業スコア", "スコア理由", "除外理由",
    "ソース", "スポカフェ掲載", "ファンスタ掲載",
    "評価", "口コミ数", "営業時間", "予算",
    "電話番号", "HP", "SNS", "最寄駅",
    "geo_quality", "ジオコーディング精度",
    "chain_flag", "sales_status",
    "名寄せ_電話キー", "名寄せ_店名キー",
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # ── stores.json ──────────────────────────────────────────────────────────
    csv_path = os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv")
    if not os.path.exists(csv_path):
        print(f"❌ マスタが見つかりません: {csv_path}")
        sys.exit(1)

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    print(f"入力: {len(rows)} 件")

    # 除外済み・スポカフェ掲載済みはルート生成に使わないが、一応全件含める
    # (filter_candidatesで除外される)
    stores = []
    for r in rows:
        # geo_quality NG かつスポカフェ掲載済みはサイズ節約のため省く
        if r.get("geo_quality") in ("NG",) and r.get("スポカフェ掲載") == "○":
            continue
        store = {k: r.get(k, "") for k in KEEP_FIELDS}
        stores.append(store)

    stores_path = os.path.join(OUT_DIR, "stores.json")
    with open(stores_path, "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(stores_path) / 1024
    print(f"✅ stores.json: {len(stores)}件 / {size_kb:.0f}KB → {stores_path}")

    # ── config.json ───────────────────────────────────────────────────────────
    cfg_src = os.path.join(BASE, "_config", "設定.json")
    with open(cfg_src, encoding="utf-8") as f:
        cfg = json.load(f)

    # Functionに必要な部分だけ抽出
    fn_cfg = {
        "route":    cfg["route"],
        "scoring":  cfg["scoring"],
        "geocoding": cfg["geocoding"],
        "team":     cfg["team"],
        "presets":  cfg.get("presets", []),
    }

    cfg_path = os.path.join(OUT_DIR, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(fn_cfg, f, ensure_ascii=False, indent=2)
    print(f"✅ config.json → {cfg_path}")
    print(f"   プリセット数: {len(fn_cfg['presets'])}")


if __name__ == "__main__":
    main()
