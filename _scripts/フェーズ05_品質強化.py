"""フェーズ0.5: ジオコーディング品質ランク化・sales列追加・S/A/B/Cスコアリング。

入力: _output/統合店舗マスタ_geocoded.csv
出力: _output/統合店舗マスタ_v2.csv

ジオコーディング完了後に実行する。
"""
import os
import csv
import json
import re
import sys

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
CONFIG_PATH = os.path.join(BASE, "_config", "設定.json")

with open(CONFIG_PATH, encoding="utf-8") as fp:
    CFG = json.load(fp)

SC = CFG["scoring"]
BASE_SCORE = SC["base"]
PENALTY = SC["penalty"]
TH = SC["thresholds"]
GENRE_TIERS = SC["sports_genre_tiers"]


# ─── ジオコーディング品質ランク ──────────────────────────────────────────────

def geo_quality_rank(lat, lng, prec_str: str) -> str:
    """精度文字列から A/B/C/NG を返す。"""
    if not lat or not lng:
        return "NG"
    prec = str(prec_str or "")
    if "詳細" in prec or re.search(r"\d+[-－]\d+", prec):
        return "A"
    if "町名" in prec or "丁目" in prec:
        return "B"
    if prec.startswith("error") or prec == "failed":
        return "NG"
    # GSI が何か返したが番地も丁目もなければ C
    return "C"


# ─── 営業優先度スコアリング ──────────────────────────────────────────────────

def genre_score(genre_str: str) -> tuple[int, str]:
    """業態ジャンル文字列からスコアと理由を返す。最高ティアのみ加算。"""
    g = str(genre_str or "")
    for tier in GENRE_TIERS:
        for kw in tier["keywords"]:
            if kw in g:
                return tier["score"], f"業態({kw})+{tier['score']}"
    return 0, ""


def _try_float(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def calc_score(row: dict) -> tuple[int, str, str]:
    """スコア・スコア理由・除外理由を返す。"""
    score = 0
    plus_reasons = []
    minus_reasons = []
    exclude_reasons = []

    # ─ プラス要素 ─
    if row.get("スポカフェ掲載") != "○":
        score += BASE_SCORE["spocafe_unlisted"]
        plus_reasons.append(f"スポカフェ未掲載+{BASE_SCORE['spocafe_unlisted']}")
    if row.get("ファンスタ掲載") != "○":
        score += BASE_SCORE["fansta_unlisted"]
        plus_reasons.append(f"ファンスタ未掲載+{BASE_SCORE['fansta_unlisted']}")

    sources = str(row.get("ソース", ""))
    if "食べログ" in sources:
        score += BASE_SCORE["source_tabelog"]
        plus_reasons.append(f"食べログ由来+{BASE_SCORE['source_tabelog']}")
    if "ダーツライブ" in sources:
        score += BASE_SCORE["source_dartslive"]
        plus_reasons.append(f"ダーツライブ由来+{BASE_SCORE['source_dartslive']}")
    if "+" in sources:
        score += BASE_SCORE["multi_source"]
        plus_reasons.append(f"複数ソース+{BASE_SCORE['multi_source']}")

    rating = _try_float(row.get("評価"))
    review = _try_float(row.get("口コミ数"))
    if rating >= SC["review_score_threshold"] or review >= SC["review_count_threshold"]:
        score += BASE_SCORE["high_review"]
        plus_reasons.append(f"評価/口コミ高+{BASE_SCORE['high_review']}")

    gs, gr = genre_score(row.get("業態ジャンル"))
    if gs > 0:
        score += gs
        plus_reasons.append(gr)

    if row.get("geo_quality") == "A":
        score += BASE_SCORE["geocode_quality_a"]
        plus_reasons.append(f"住所精度A+{BASE_SCORE['geocode_quality_a']}")

    # ─ マイナス要素 ─
    if row.get("スポカフェ掲載") == "○":
        score += PENALTY["spocafe_listed"]
        minus_reasons.append("スポカフェ掲載済")
        exclude_reasons.append("スポカフェ掲載済")
    if row.get("geo_quality") in ("C", "NG"):
        score += PENALTY["geocode_quality_c_or_ng"]
        minus_reasons.append(f"住所精度{row.get('geo_quality')}")
        exclude_reasons.append(f"住所精度{row.get('geo_quality')}→ルート除外")
    if str(row.get("sales_status", "")).strip() == "NG":
        score += PENALTY["sales_status_ng"]
        minus_reasons.append("訪問NG済")
        exclude_reasons.append("訪問NG済")
    if str(row.get("ソース", "")).strip() == "スペースマーケット":
        score += PENALTY.get("spacemarket_only", -15)
        minus_reasons.append("スペースマーケット単一ソース")
    rental_kws = ["スペース貸", "レンタルスペース", "カラオケスペース", "パーティースペース"]
    genre_str = str(row.get("業態ジャンル", ""))
    if any(kw in genre_str for kw in rental_kws):
        score += PENALTY.get("rental_space_genre", -25)
        minus_reasons.append("スペース貸し業態")
        exclude_reasons.append("スペース貸し業態→除外")
    phone_digits = re.sub(r"[^\d]", "", str(row.get("電話番号", "") or ""))
    if phone_digits.startswith(("0120", "0800", "0570")):
        score += PENALTY.get("free_dial", -10)
        minus_reasons.append("フリーダイヤル(チェーン疑い)")

    score = max(score, -100)

    # スコア理由文
    reason_parts = plus_reasons + ([f"減点: {', '.join(minus_reasons)}"] if minus_reasons else [])
    score_reason = " / ".join(reason_parts)

    exclude_reason = ", ".join(exclude_reasons) if exclude_reasons else ""
    return score, score_reason, exclude_reason


def score_to_rank(score: int) -> str:
    if score >= TH["S"]:
        return "S"
    if score >= TH["A"]:
        return "A"
    if score >= TH["B"]:
        return "B"
    if score >= TH["C"]:
        return "C"
    return "除外"


# ─── 新規 sales 列（デフォルト空） ─────────────────────────────────────────

SALES_COLS = [
    "sales_status",       # 未接触/訪問済/架電済/興味あり/掲載済/NG/再訪候補
    "last_contact_date",  # 最終接触日 YYYY-MM-DD
    "contact_method",     # 飛び込み/電話/メール/紹介
    "result",             # 名刺獲得/担当不在/興味あり/NG 等
    "next_action",        # 再訪/電話/資料送付/放置/完了
    "owner",              # 担当者名（チームメンバー）
    "memo",               # 営業メモ（自由記述）
]


def main():
    in_csv = os.path.join(BASE, "_output", "統合店舗マスタ_geocoded.csv")
    out_csv = os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv")

    if not os.path.exists(in_csv):
        print(f"❌ 入力ファイルが見つかりません: {in_csv}")
        print("   ジオコーディング.py の完了後に実行してください。")
        sys.exit(1)

    rows = list(csv.DictReader(open(in_csv, encoding="utf-8-sig")))
    print(f"入力: {len(rows)} 件")

    # geo_quality を A/B/C/NG に改訂 + スコア計算
    for r in rows:
        r["geo_quality"] = geo_quality_rank(r.get("緯度"), r.get("経度"), r.get("ジオコーディング精度"))
        score, score_reason, exclude_reason = calc_score(r)
        r["営業スコア"] = score
        r["営業ランク"] = score_to_rank(score)
        r["スコア理由"] = score_reason
        r["除外理由"] = exclude_reason
        # chain_flag: フリーダイヤルはチェーン・大型FC疑い
        phone_digits = re.sub(r"[^\d]", "", str(r.get("電話番号", "") or ""))
        r["chain_flag"] = "チェーン疑" if phone_digits.startswith(("0120", "0800", "0570")) else ""
        # sales列（既存値があれば保持、なければ空文字）
        for col in SALES_COLS:
            if col not in r:
                r[col] = ""

    # 出力列順
    existing_cols = list(rows[0].keys())
    idx = existing_cols.index("ジオコーディング精度") if "ジオコーディング精度" in existing_cols else len(existing_cols)
    new_cols = (
        existing_cols[:idx + 1]
        + ["geo_quality"]
        + [c for c in existing_cols[idx + 1:] if c != "geo_quality"]
        + ["営業スコア", "営業ランク", "スコア理由", "除外理由", "chain_flag"]
        + SALES_COLS
    )
    # 重複除去しつつ順序保持
    seen = set()
    fieldnames = [c for c in new_cols if not (c in seen or seen.add(c))]

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # サマリ
    by_rank = {}
    for r in rows:
        by_rank[r["営業ランク"]] = by_rank.get(r["営業ランク"], 0) + 1
    geo_by_q = {}
    for r in rows:
        geo_by_q[r["geo_quality"]] = geo_by_q.get(r["geo_quality"], 0) + 1

    print("=" * 50)
    print(f"出力: {out_csv}")
    print("【営業ランク分布】")
    for rank in ("S", "A", "B", "C", "除外"):
        print(f"  {rank}: {by_rank.get(rank, 0)}")
    print("【ジオコーディング品質】")
    for q in ("A", "B", "C", "NG"):
        print(f"  {q}: {geo_by_q.get(q, 0)}")
    route_ok = geo_by_q.get("A", 0) + geo_by_q.get("B", 0)
    print(f"  ルート提案可能（A+B）: {route_ok}")


if __name__ == "__main__":
    main()
