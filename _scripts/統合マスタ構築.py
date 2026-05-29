"""フェーズ0: 3ソース＋既存マスタを統合し、1行1店舗の統合店舗マスタを作る。

処理: 正規化スキーマへマッピング → 名寄せ・重複排除 → スポカフェ/ファンスタ掲載突合
出力: _output/統合店舗マスタ.csv（ジオコーディング前）

ジオコーディング（緯度経度付与）は ジオコーディング.py で別途実行する。
"""
import os
import sys
import csv
import json
import hashlib
import shutil
import tempfile

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import norm_name, norm_phone, norm_address, extract_pref_city  # noqa

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
RAW = os.path.join(ROOT, "訪問店舗提案サービス", "_data", "raw")
MASTER = os.path.join(ROOT, "_マスタデータ")
OUT = os.path.join(ROOT, "訪問店舗提案サービス", "_output")
DATA = os.path.join(ROOT, "訪問店舗提案サービス", "_data")
TMP = tempfile.gettempdir()

# 永続IDマップ: merge_key → 店舗ID。再実行でもIDを変えない。
ID_MAP_PATH = os.path.join(DATA, "store_id_map.json")


def _merge_key(phone_key: str, name_key: str) -> str:
    """名寄せの一意キー。電話番号優先。"""
    return phone_key if phone_key else name_key


def _hash_id(key: str) -> str:
    """キーからSHA256の先頭8桁で店舗ID（例: S1A2B3C4）を生成。"""
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8].upper()
    return "S" + h


def load_id_map() -> dict:
    if os.path.exists(ID_MAP_PATH):
        with open(ID_MAP_PATH, encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_id_map(id_map: dict) -> None:
    with open(ID_MAP_PATH, "w", encoding="utf-8") as fp:
        json.dump(id_map, fp, ensure_ascii=False, indent=2)


def assign_id(phone_key: str, name_key: str, id_map: dict) -> str:
    """既存マップにあればそのID、なければ新規生成して登録する。"""
    key = _merge_key(phone_key, name_key)
    if not key:
        return "S00000000"
    if key in id_map:
        return id_map[key]
    sid = _hash_id(key)
    # ハッシュ衝突時は末尾に連番付与
    base = sid
    n = 1
    while sid in id_map.values():
        sid = base[:-2] + f"{n:02d}"
        n += 1
    id_map[key] = sid
    return sid

# 統合スキーマ（出力列順）
COLUMNS = [
    "店舗ID", "店名", "業態ジャンル", "電話番号", "住所",
    "最寄駅", "営業時間", "予算", "評価", "口コミ数",
    "HP", "SNS", "ソース",
    "スポカフェ掲載", "ファンスタ掲載", "営業ターゲット",
    "緯度", "経度", "ジオコーディング精度",
    "名寄せ_電話キー", "名寄せ_店名キー",
]


def load_xlsx(path):
    tmp = os.path.join(TMP, "ld_" + str(abs(hash(path)) % 10**8) + ".xlsx")
    shutil.copy2(path, tmp)
    wb = openpyxl.load_workbook(tmp, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    out = []
    for r in rows[1:]:
        if all(c is None for c in r):
            continue
        out.append({header[i]: r[i] for i in range(len(header))})
    return out


def g(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def from_tabelog(rows):
    out = []
    for r in rows:
        out.append({
            "店名": g(r, "店舗名"),
            "業態ジャンル": g(r, "ジャンル"),
            "電話番号": g(r, "電話番号"),
            "住所": g(r, "住所"),
            "最寄駅": g(r, "交通手段"),
            "営業時間": g(r, "営業時間"),
            "予算": g(r, "予算"),
            "評価": g(r, "評価"),
            "口コミ数": g(r, "口コミ数"),
            "HP": g(r, "ホームページ"),
            "SNS": g(r, "公式アカウント"),
            "ソース": "食べログ",
        })
    return out


def from_dartslive(rows):
    out = []
    for r in rows:
        out.append({
            "店名": g(r, "店舗名"),
            "業態ジャンル": g(r, "店舗業態"),
            "電話番号": g(r, "電話番号"),
            "住所": g(r, "住所"),
            "最寄駅": g(r, "最寄り駅"),
            "営業時間": g(r, "営業時間"),
            "予算": g(r, "予算", "料金帯"),
            "評価": "",
            "口コミ数": "",
            "HP": g(r, "店舗HP"),
            "SNS": g(r, "Instagram", "X"),
            "ソース": "ダーツライブ",
        })
    return out


def from_spacemarket(rows):
    out = []
    for r in rows:
        out.append({
            "店名": g(r, "店舗名(ページタイトル)"),
            "業態ジャンル": g(r, "会場タイプ", "タグ"),
            "電話番号": "",
            "住所": g(r, "住所"),
            "最寄駅": g(r, "最寄駅"),
            "営業時間": "",
            "予算": g(r, "プラン"),
            "評価": g(r, "評価"),
            "口コミ数": g(r, "評価総数"),
            "HP": g(r, "ページURL"),
            "SNS": "",
            "ソース": "スペースマーケット",
        })
    return out


def load_spocafe_master(path):
    """ID行→店名行→タブ区切り行 のレコード形式をパース。
    返り値: {正規化店名キー: {店名, 都道府県, 市区町村}}"""
    with open(path, encoding="utf-8") as fp:
        lines = [ln.rstrip("\n") for ln in fp]
    listed = {}
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i].strip()
        if ln.isdigit():  # ID行
            name = lines[i + 1].strip() if i + 1 < n else ""
            tab = lines[i + 2].split("\t") if i + 2 < n else []
            pref = tab[1].strip() if len(tab) > 1 else ""
            city = tab[2].strip() if len(tab) > 2 else ""
            key = norm_name(name)
            if key:
                listed[key] = {"店名": name, "都道府県": pref, "市区町村": city}
            i += 3
        else:
            i += 1
    return listed


def load_fansta_master(path):
    rows = load_xlsx(path)
    listed = {}
    for r in rows:
        name = g(r, "店舗名")
        key = norm_name(name)
        if key:
            listed[key] = {"店名": name, "電話": norm_phone(g(r, "電話番号"))}
    return listed


def merge(records):
    """名寄せ・重複排除。電話キー優先、無ければ店名キーで統合。"""
    by_phone = {}
    by_name = {}
    merged = []

    def new_entry(rec):
        e = dict(rec)
        e["ソース"] = {rec["ソース"]}
        merged.append(e)
        return e

    for rec in records:
        pk = norm_phone(rec["電話番号"])
        nk = norm_name(rec["店名"])
        target = None
        if pk and pk in by_phone:
            target = by_phone[pk]
        elif nk and nk in by_name:
            target = by_name[nk]
        if target is None:
            target = new_entry(rec)
        else:
            target["ソース"].add(rec["ソース"])
            for col in ["業態ジャンル", "電話番号", "住所", "最寄駅",
                        "営業時間", "予算", "評価", "口コミ数", "HP", "SNS"]:
                if not target.get(col) and rec.get(col):
                    target[col] = rec[col]
        if pk:
            by_phone[pk] = target
        if nk:
            by_name[nk] = target
    return merged


def main():
    os.makedirs(OUT, exist_ok=True)
    print("ソース読み込み中...")
    tabelog = from_tabelog(load_xlsx(os.path.join(RAW, "食べログ_バー夜22時以降スポーツ観戦OK.xlsx")))
    dartslive = from_dartslive(load_xlsx(os.path.join(RAW, "ダーツライブ設置店.xlsx")))
    spacemarket = from_spacemarket(load_xlsx(os.path.join(RAW, "スペースマーケット_スポーツ観戦スペース.xlsx")))
    print(f"  食べログ={len(tabelog)} ダーツ={len(dartslive)} スペマ={len(spacemarket)}")

    all_recs = tabelog + dartslive + spacemarket
    print(f"名寄せ前 合計={len(all_recs)}")
    merged = merge(all_recs)
    print(f"名寄せ後 ユニーク店舗={len(merged)}")

    print("掲載マスタ突合中...")
    spocafe = load_spocafe_master(os.path.join(MASTER, "店舗一覧マスタ_20260331.txt"))
    fansta = load_fansta_master(os.path.join(MASTER, "ファンスタ収集データ_20260416.xlsx"))
    print(f"  スポカフェ掲載={len(spocafe)} ファンスタ掲載={len(fansta)}")

    print("店舗IDを割り当て中（永続IDマップ使用）...")
    id_map = load_id_map()
    prev_count = len(id_map)

    for e in merged:
        nk = norm_name(e["店名"])
        pk = norm_phone(e["電話番号"])
        e["店舗ID"] = assign_id(pk, nk, id_map)
        nk = norm_name(e["店名"])
        pk = norm_phone(e["電話番号"])
        e["スポカフェ掲載"] = "○" if nk in spocafe else ""
        in_fansta = nk in fansta or (pk and any(f.get("電話") == pk for f in fansta.values()))
        e["ファンスタ掲載"] = "○" if in_fansta else ""
        e["営業ターゲット"] = "" if (e["スポカフェ掲載"] or e["ファンスタ掲載"]) else "★"
        e["緯度"] = ""
        e["経度"] = ""
        e["ジオコーディング精度"] = ""
        e["名寄せ_電話キー"] = pk
        e["名寄せ_店名キー"] = nk
        e["住所"] = norm_address(e["住所"])
        e["ソース"] = "+".join(sorted(e["ソース"]))

    save_id_map(id_map)
    new_ids = len(id_map) - prev_count
    print(f"  新規ID発行: {new_ids}件 / 既存ID再利用: {len(merged) - new_ids}件")

    out_path = os.path.join(OUT, "統合店舗マスタ.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for e in merged:
            w.writerow(e)

    n_target = sum(1 for e in merged if e["営業ターゲット"])
    n_spo = sum(1 for e in merged if e["スポカフェ掲載"])
    n_fan = sum(1 for e in merged if e["ファンスタ掲載"])
    multi = sum(1 for e in merged if "+" in e["ソース"])
    print("=" * 50)
    print(f"出力: {out_path}")
    print(f"  ユニーク店舗 : {len(merged)}")
    print(f"  複数ソース掲載: {multi}")
    print(f"  スポカフェ掲載: {n_spo}")
    print(f"  ファンスタ掲載: {n_fan}")
    print(f"  営業ターゲット(未掲載★): {n_target}")


if __name__ == "__main__":
    main()
