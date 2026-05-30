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
LISTING = SC["listing"]
ENG = SC["engagement"]
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


def _engagement(row: dict, plus_reasons: list) -> int:
    """掲載状況以外の「店の魅力・温度」を表す補助点。
    掲載店の並び順と、未掲載店のB/C判定に使う。"""
    eng = 0
    sources = str(row.get("ソース", ""))

    # 食べログ単独の由来加点は廃止（優先度最低）。スポーツ観戦の実機材フラグのみ加点。
    if "食べログ" in sources and str(row.get("スポーツ設備", "")).strip():
        eng += ENG["tabelog_sports_facility"]
        plus_reasons.append(f"スポーツ設備有+{ENG['tabelog_sports_facility']}")
    if "ダーツライブ" in sources:
        eng += ENG["source_dartslive"]
        plus_reasons.append(f"ダーツライブ由来+{ENG['source_dartslive']}")
    if "+" in sources:
        eng += ENG["multi_source"]
        plus_reasons.append(f"複数ソース+{ENG['multi_source']}")

    rating = _try_float(row.get("評価"))
    review = _try_float(row.get("口コミ数"))
    if rating >= SC["review_score_threshold"] or review >= SC["review_count_threshold"]:
        eng += ENG["high_review"]
        plus_reasons.append(f"評価/口コミ高+{ENG['high_review']}")

    gs, gr = genre_score(row.get("業態ジャンル"))
    if gs > 0:
        eng += gs
        plus_reasons.append(gr)

    if row.get("geo_quality") == "A":
        eng += ENG["geocode_quality_a"]
        plus_reasons.append(f"住所精度A+{ENG['geocode_quality_a']}")

    # チェーン疑い（フリーダイヤル）・スペースマーケット単一は温度を下げる
    phone_digits = re.sub(r"[^\d]", "", str(row.get("電話番号", "") or ""))
    if phone_digits.startswith(("0120", "0800", "0570")):
        eng += PENALTY.get("free_dial", -10)
        plus_reasons.append("フリーダイヤル(チェーン疑い)")
    if str(row.get("ソース", "")).strip() == "スペースマーケット":
        eng += PENALTY.get("spacemarket_only", -15)
        plus_reasons.append("スペマ単一ソース")
    return eng


def calc_score(row: dict) -> tuple[int, str, str, str]:
    """(営業スコア, 営業ランク, スコア理由, 除外理由) を返す。

    ランクは「掲載状況」で決まる:
      スポカフェ＋ファンスタ両方掲載 → S（集客意欲が最も高く有料転換しやすい）
      スポカフェのみ掲載            → AS（無料→有料転換／上位アップセルの本命）
      ファンスタのみ掲載            → AF（競合のみ→スポカフェへ奪取）
      どちらも未掲載（コールド）    → 補助点で B / C
    掲載店は有料プラン契約済みでも残す（上位プランへのアップセル対象）。
    """
    plus_reasons = []
    exclude_reasons = []

    spo = row.get("スポカフェ掲載") == "○"
    fan = row.get("ファンスタ掲載") == "○"
    plan = str(row.get("スポカフェプラン", "")).strip()

    eng = _engagement(row, plus_reasons)

    # スポカフェ無料掲載＝有料転換の本命、有料契約＝上位プランへのアップセル
    if spo:
        if plan and plan != "フリー":
            plus_reasons.append(f"スポカフェ有料({plan})→上位転換")
        else:
            eng += ENG.get("spocafe_free_upsell", 0)
            plus_reasons.append(f"スポカフェ無料掲載→有料転換+{ENG.get('spocafe_free_upsell', 0)}")
    if fan and not spo:
        eng += ENG.get("fansta_only_takeover", 0)

    # ── 掲載コンボでランク確定 ──
    if spo and fan:
        rank = "S"
        score = LISTING["both_base"] + eng
        plus_reasons.insert(0, "スポカフェ＋ファンスタ両方掲載＝最優先")
    elif spo:
        rank = "AS"
        score = LISTING["one_base"] + eng
        plus_reasons.insert(0, "スポカフェ掲載(無料→有料転換／上位アップセル)")
    elif fan:
        rank = "AF"
        score = LISTING.get("one_base_fansta", LISTING["one_base"] - 20) + eng
        plus_reasons.insert(0, "ファンスタのみ掲載(競合→奪取)")
    else:
        score = eng
        rank = "B" if eng >= LISTING["none_b_threshold"] else "C"
        plus_reasons.insert(0, "未掲載(新規開拓)")

    # ── ハード除外（ランクに優先） ──
    if str(row.get("sales_status", "")).strip() == "NG":
        rank = "除外"
        score = -100
        exclude_reasons.append("訪問NG済")
    rental_kws = ["スペース貸", "レンタルスペース", "カラオケスペース", "パーティースペース"]
    if any(kw in str(row.get("業態ジャンル", "")) for kw in rental_kws):
        rank = "除外"
        score = min(score, -50)
        exclude_reasons.append("スペース貸し業態→除外")
    # ジオコーディング品質はルート可否のみ（ランクは保持、filterで除外）
    if row.get("geo_quality") in ("C", "NG"):
        exclude_reasons.append(f"住所精度{row.get('geo_quality')}→ルート除外")

    score = max(score, -100)
    reason_parts = plus_reasons + (
        [f"除外: {', '.join(exclude_reasons)}"] if exclude_reasons else [])
    score_reason = " / ".join(reason_parts)
    exclude_reason = ", ".join(exclude_reasons) if exclude_reasons else ""
    return score, rank, score_reason, exclude_reason


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

    # スポカフェ掲載店の補完を取り込む。
    #   POI(04c, 精度A・実座標) を優先し、POIで取れなかった店だけ地名概算(04b, B) で補う。
    base_cols = set(rows[0].keys()) if rows else set()
    suppl_files = [
        ("POI", os.path.join(BASE, "_output", "補完_スポカフェ掲載店_POI.csv")),
        ("地名概算", os.path.join(BASE, "_output", "補完_スポカフェ掲載店.csv")),
    ]
    seen_ids = set()
    added = 0
    for label, path in suppl_files:
        if not os.path.exists(path):
            continue
        suppl = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        kept = []
        for s in suppl:
            sid = s.get("店舗ID", "")
            if sid and sid in seen_ids:
                continue  # 既にPOIで採用済み → 地名概算はスキップ
            seen_ids.add(sid)
            for c in base_cols:
                s.setdefault(c, "")
            kept.append(s)
        rows.extend(kept)
        added += len(kept)
        print(f"  ＋スポカフェ掲載店補完({label}): {len(kept)} 件")
    if added:
        print(f"  → 補完合計 {added} 件 / 計 {len(rows)} 件")

    # geo_quality を A/B/C/NG に改訂 + スコア計算
    for r in rows:
        r["geo_quality"] = geo_quality_rank(r.get("緯度"), r.get("経度"), r.get("ジオコーディング精度"))
        score, rank, score_reason, exclude_reason = calc_score(r)
        r["営業スコア"] = score
        r["営業ランク"] = rank
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
    for rank in ("S", "AS", "AF", "B", "C", "除外"):
        print(f"  {rank}: {by_rank.get(rank, 0)}")
    print("【ジオコーディング品質】")
    for q in ("A", "B", "C", "NG"):
        print(f"  {q}: {geo_by_q.get(q, 0)}")
    route_ok = geo_by_q.get("A", 0) + geo_by_q.get("B", 0)
    print(f"  ルート提案可能（A+B）: {route_ok}")


if __name__ == "__main__":
    main()
