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
import re  # noqa
from normalize import (  # noqa
    norm_name, norm_phone, norm_address, extract_pref_city,
    to_halfwidth, _NAME_NOISE,
)

# 突合用：店名の先頭に付く業態接頭辞（剥がして別名キーを作る）
_GENRE_PREFIX = re.compile(
    r"^(スポーツバー|スポーツダイニング|スポーツカフェ|ダーツバー|ダーツ＆バー|ダーツ&バー|"
    r"ダイニングバー|カフェ＆バー|カフェ&バー|カフェバー|ビアバー|ワインバー|"
    r"sports?\s*bar|darts?\s*bar|bar|cafe)\s*",
    re.IGNORECASE,
)


def name_keys(name: str) -> set:
    """掲載マスタ突合用に、1つの店名から表記ゆれを吸収した複数キーを生成。

    - 通常の norm_name キー
    - 「/」「／」で区切られた各パート（例: "Deportes/デポルテス" → "deportes" も拾う）
    - 業態接頭辞を剥がしたキー（例: "スポーツバー PAPABAMP" → "papabamp"）
    短すぎる断片（誤マッチ源）は除外する。
    """
    keys = set()
    if not name:
        return keys
    base = norm_name(name)
    if base:
        keys.add(base)
    raw = _NAME_NOISE.sub("", to_halfwidth(name))
    for part in re.split(r"[/／]", raw):
        k = norm_name(part)
        if len(k) >= 4:
            keys.add(k)
    stripped = _GENRE_PREFIX.sub("", to_halfwidth(name))
    ks = norm_name(stripped)
    if len(ks) >= 4:
        keys.add(ks)
    return keys

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
    "HP", "SNS", "ソース", "スポーツ設備",
    "スポカフェ掲載", "スポカフェプラン", "ファンスタ掲載", "営業ターゲット",
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


# 食べログ「空間・設備」からスポーツ観戦の"実機材"を示すフラグを抽出。
# 注意: この食べログ元データは「スポーツ観戦OK」で抽出済みのため『スポーツ観戦可』は
# 全店100%に付く＝判別力ゼロ。よって基準語は除外し、実際に観戦環境の差がつく
# 設備語（プロジェクター/大型ビジョン等）だけを加点条件とする（現データではプロジェクター22%）。
_SPORTS_FACILITY_KW = [
    "プロジェクター", "大型ビジョン", "大型スクリーン", "大画面", "モニター",
    "ビジョン", "スクリーン", "DAZN", "パブリックビューイング",
]


def _extract_sports_facility(*texts) -> str:
    """設備・シーン文字列群から該当キーワードを拾い、"、"区切りで返す。"""
    blob = " ".join(t for t in texts if t)
    found = [kw for kw in _SPORTS_FACILITY_KW if kw in blob]
    # 重複（スクリーン⊂大型スクリーン 等）はそのまま列挙でも害はないが整理
    return "、".join(dict.fromkeys(found))


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
            "スポーツ設備": _extract_sports_facility(
                g(r, "空間・設備"), g(r, "利用シーン"), g(r, "サービス")),
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
    # タブ区切り行の列位置: 0国 1都道府県 2その他住所 3地名 4スポーツ
    #                       5その他 6メモ 7プラン 8ランク 9自動更新 10状態 11AC
    while i < n:
        ln = lines[i].strip()
        if ln.isdigit():  # ID行
            name = lines[i + 1].strip() if i + 1 < n else ""
            tab = lines[i + 2].split("\t") if i + 2 < n else []
            pref = tab[1].strip() if len(tab) > 1 else ""
            city = tab[2].strip() if len(tab) > 2 else ""
            plan = tab[7].strip() if len(tab) > 7 else ""
            state = tab[10].strip() if len(tab) > 10 else ""
            i += 3
            # 状態が「掲載」のレコードのみ"掲載中"として扱う
            #（状態空欄＝取り下げ/未公開はスポカフェ掲載とみなさない）
            if state != "掲載":
                continue
            info = {"店名": name, "都道府県": pref, "市区町村": city, "プラン": plan}
            # 表記ゆれ吸収のため複数キーで登録（先勝ち＝完全一致キーを優先）
            for key in name_keys(name):
                listed.setdefault(key, info)
        else:
            i += 1
    return listed


def load_fansta_master(path):
    rows = load_xlsx(path)
    listed = {}
    for r in rows:
        name = g(r, "店舗名")
        info = {"店名": name, "電話": norm_phone(g(r, "電話番号"))}
        for key in name_keys(name):
            listed.setdefault(key, info)
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
                        "営業時間", "予算", "評価", "口コミ数", "HP", "SNS",
                        "スポーツ設備"]:
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
        rec_keys = name_keys(e["店名"])
        spo_hit = rec_keys & spocafe.keys()
        e["スポカフェ掲載"] = "○" if spo_hit else ""
        # 掲載中ならプランも保持（フリー＝無料掲載＝有料転換の営業先）
        e["スポカフェプラン"] = spocafe[next(iter(spo_hit))]["プラン"] if spo_hit else ""
        in_fansta = bool(rec_keys & fansta.keys()) or (
            pk and any(f.get("電話") == pk for f in fansta.values()))
        e["ファンスタ掲載"] = "○" if in_fansta else ""
        # 掲載店（集客意欲あり＝有料転換/奪取の営業先）に★。未掲載はコールド。
        e["営業ターゲット"] = "★" if (e["スポカフェ掲載"] or e["ファンスタ掲載"]) else ""
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
