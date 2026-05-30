"""スポカフェ掲載店マスタへ「番地付き住所」を還元するための納品データを生成する。

背景:
  スポカフェ掲載店マスタ（店舗一覧マスタ_*.txt）は番地が無く、市区町村＋地名タグのみ。
  これを Google Places Text Search（店名＋市区町村）で実在店舗の座標・番地付き住所に
  解決した結果（_data/places_cache.json）を、元マスタの「店舗ID」付きで書き出す。
  エンジニアは店舗IDで元マスタへJOINして住所・座標を還元できる。

入力:
  - _マスタデータ/店舗一覧マスタ_*.txt   （3行1レコード: ID / 店名 / タブ区切り住所列）
  - _data/places_cache.json              （フェーズ04cで作成。query→{lat,lng,addr}）

出力（納品フォルダ）:
  - スポカフェ掲載店_住所補完データ_YYYYMMDD.csv   採用1,163件（市区町村検証済・精度A）
  - 未解決リスト_要手当て_YYYYMMDD.csv             89件（別市除外86＋未発見3）

注意: APIは呼ばない。キャッシュ済みデータのみから生成するため無料・再現可能。
"""
import os
import re
import csv
import glob
import json
import datetime

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
MASTER = sorted(glob.glob(os.path.join(ROOT, "_マスタデータ", "店舗一覧マスタ_*.txt")))[-1]
CACHE = os.path.join(BASE, "_data", "places_cache.json")

TODAY = datetime.date.today().strftime("%Y%m%d")
OUT_DIR = os.path.join(BASE, f"_納品_スポカフェ掲載店住所還元_{TODAY}")
OUT_MAIN = os.path.join(OUT_DIR, f"スポカフェ掲載店_住所補完データ_{TODAY}.csv")
OUT_CHECK = os.path.join(OUT_DIR, f"要確認リスト_区地名不一致_{TODAY}.csv")
OUT_TODO = os.path.join(OUT_DIR, f"未解決リスト_要手当て_{TODAY}.csv")

_POSTAL = re.compile(r"〒?\s*(\d{3}-?\d{4})")


def clean_name(n: str) -> str:
    n = re.sub(r"\(.*$", "", n)
    n = re.sub(r"｜.*$", "", n)
    return n.strip()


def _city_tokens(city: str):
    """元マスタの市区町村は「名古屋市,中区」のようにカンマ連結（市＋区/地名タグ）。"""
    return [t.strip() for t in re.split(r"[,，]", city or "") if t.strip()]


def within_city_loose(fmt: str, pref: str, city: str) -> bool:
    """市レベルの一致判定（別の市区町村への誤一致＝未解決を弾く）。
    フェーズ04cの判定と同一にして件数（別市86件）を再現する。"""
    if not fmt:
        return False
    if city and city in fmt:
        return True
    base = re.sub(r"市.*$", "市", city) if "市" in city else city
    return bool(base and base in fmt)


def within_city_strict(fmt: str, pref: str, city: str) -> bool:
    """市までしか一致しない『区違い』（例: 中区→中村区）を弾くため、
    元マスタの市区町村トークン（市・区・地名）が全て取得住所に含まれることを要求する。"""
    if not fmt:
        return False
    toks = _city_tokens(city)
    if not toks:
        return False
    return all(t in fmt for t in toks)


def split_postal(addr: str):
    """formattedAddress から郵便番号を分離して (郵便番号, 〒抜き住所) を返す。"""
    m = _POSTAL.search(addr or "")
    if not m:
        return "", (addr or "").strip()
    zipcode = m.group(1)
    rest = (addr[:m.start()] + addr[m.end():]).strip()
    return zipcode, rest


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cache = json.load(open(CACHE, encoding="utf-8"))
    lines = open(MASTER, encoding="utf-8").read().splitlines()

    main_rows, check_rows, todo_rows = [], [], []
    i = 2
    while i + 2 <= len(lines):
        sid, name = lines[i], lines[i + 1]
        cols = lines[i + 2].split("\t")
        i += 3
        if len(cols) <= 10 or cols[10].strip() != "掲載":
            continue
        pref = cols[1].strip() if len(cols) > 1 else ""
        city = cols[2].strip() if len(cols) > 2 else ""
        plan = cols[7].strip() if len(cols) > 7 else ""
        q = f"{clean_name(name)} {city} {pref}"
        if q not in cache:        # POI送信対象（統合マスタ未マッチの掲載店）だけがキャッシュにある
            continue
        c = cache[q]
        if not c:
            todo_rows.append([sid, name, pref, city, plan, "Google Placesで未発見"])
            continue
        addr = c.get("addr", "")
        if not within_city_loose(addr, pref, city):
            # 市そのものが違う＝別の市区町村に誤一致
            todo_rows.append([sid, name, pref, city, plan, f"別の市区町村に一致（{addr[:30]}）"])
            continue
        if not within_city_strict(addr, pref, city):
            # 市は合うが区/地名タグが不一致（政令市の区違い等）→ 本番反映前に要確認
            check_rows.append([sid, name, pref, city, plan, addr[:40],
                               "市は一致するが区/地名タグが不一致（区違いの可能性）"])
            continue
        zipcode, addr_clean = split_postal(addr)
        main_rows.append([
            sid, name, pref, city, zipcode, addr_clean,
            c.get("lat", ""), c.get("lng", ""), plan,
            "Google Places Text Search（店名＋市区町村）",
            "A（店名POI一致・市区町村トークン全一致検証済）",
        ])

    with open(OUT_MAIN, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["スポカフェ店舗ID", "店名", "都道府県", "市区町村",
                    "郵便番号", "補完住所", "緯度", "経度", "プラン",
                    "取得方法", "信頼度"])
        w.writerows(main_rows)

    with open(OUT_CHECK, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["スポカフェ店舗ID", "店名", "都道府県", "市区町村",
                    "プラン", "取得住所", "要確認理由"])
        w.writerows(check_rows)

    with open(OUT_TODO, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["スポカフェ店舗ID", "店名", "都道府県", "市区町村", "プラン", "未解決理由"])
        w.writerows(todo_rows)

    print(f"納品フォルダ: {OUT_DIR}")
    print(f"  住所補完データ(本体): {len(main_rows)} 件 -> {os.path.basename(OUT_MAIN)}")
    print(f"  要確認リスト        : {len(check_rows)} 件 -> {os.path.basename(OUT_CHECK)}")
    print(f"  未解決リスト        : {len(todo_rows)} 件 -> {os.path.basename(OUT_TODO)}")


if __name__ == "__main__":
    main()
