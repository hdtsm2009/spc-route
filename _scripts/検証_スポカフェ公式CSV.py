# -*- coding: utf-8 -*-
"""Step0a: スポカフェ新公式エクスポートの preflight 検証（件数定義の確定）。

目的: 以後の全工程の基準となる「掲載件数」を確定し、同名/電話の衝突を洗い出す。
ヘッダー名参照（列番号非依存）。必須列が無ければ fail fast。

出力:
  _output/_検証_重複キー.csv    (norm_name,pref,city,件数,店名一覧,住所一覧,電話一覧)
  _output/_検証_電話衝突.csv    (電話, 件数, 店舗ID一覧, 店名一覧, 住所一覧)  ※同一電話に複数店
標準出力: ステータス別件数・pref/city空・数値ID重複 等
"""
import os
import sys
import csv
import glob
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import norm_name, norm_phone, extract_pref_city  # noqa

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"


def latest_official():
    """_マスタデータ の最新の スポカフェ公式エクスポート_*.csv を返す（定期取込対応）。"""
    cands = sorted(glob.glob(os.path.join(ROOT, "_マスタデータ", "スポカフェ公式エクスポート_*.csv")))
    if not cands:
        raise SystemExit("公式エクスポートが _マスタデータ に見つかりません")
    return cands[-1]


SRC = latest_official()
OUT = os.path.join(ROOT, "訪問店舗提案サービス", "_output")

REQUIRED = ["店舗ID", "ステータス", "住所", "建物", "電話番号", "郵便番号",
            "タグ：都道府県", "タグ：市区町村", "プラン", "店舗名"]


def pref_city(row):
    pref = (row.get("タグ：都道府県") or "").strip()
    city = (row.get("タグ：市区町村") or "").strip()
    if not pref or not city:
        p, c = extract_pref_city(row.get("住所", ""))
        pref = pref or p
        city = city or c
    return pref, city


def main():
    if not os.path.exists(SRC):
        print(f"❌ 公式CSVが無い: {SRC}"); sys.exit(1)
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    if not rows:
        print("❌ 空CSV"); sys.exit(1)
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        print(f"❌ 必須列が無い（fail fast）: {missing}"); sys.exit(1)

    # ステータス別
    st = collections.Counter((r.get("ステータス") or "(空)").strip() for r in rows)
    listed = [r for r in rows if (r.get("ステータス") or "").strip() == "掲載"]
    print(f"総レコード: {len(rows)}")
    print("ステータス別:", dict(st))
    print(f"★ 掲載（以後の基準）: {len(listed)}")

    # pref/city 空（掲載のみ）
    tag_empty = sum(1 for r in listed if not (r.get("タグ：市区町村") or "").strip())
    after_fb = sum(1 for r in listed if not pref_city(r)[1])
    print(f"市区町村タグ空: {tag_empty} → 住所フォールバック後も空: {after_fb}")

    # 数値ID重複
    idc = collections.Counter((r.get("店舗ID") or "").strip() for r in listed)
    dup_ids = {k: v for k, v in idc.items() if v > 1 and k}
    print(f"数値ID重複: {len(dup_ids)} 種")

    # (norm_name,pref,city) 重複
    npc = collections.defaultdict(list)
    for r in listed:
        p, c = pref_city(r)
        npc[(norm_name(r.get("店舗名", "")), p, c)].append(r)
    dup_npc = {k: v for k, v in npc.items() if len(v) > 1}
    # norm_name 単体重複
    nn = collections.Counter(norm_name(r.get("店舗名", "")) for r in listed)
    dup_name_only = sum(1 for k, v in nn.items() if v > 1 and k)

    # 電話衝突（同一電話に複数の異なる数値ID）
    ph = collections.defaultdict(list)
    for r in listed:
        pk = norm_phone(r.get("電話番号", ""))
        if pk:
            ph[pk].append(r)
    phone_conf = {k: v for k, v in ph.items()
                  if len({(x.get("店舗ID") or "").strip() for x in v}) > 1}

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "_検証_重複キー.csv"), "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["norm_name", "都道府県", "市区町村", "件数", "店名一覧", "住所一覧", "電話一覧"])
        for (nm, p, c), vs in sorted(dup_npc.items(), key=lambda x: -len(x[1])):
            w.writerow([nm, p, c, len(vs),
                        " | ".join(x.get("店舗名", "") for x in vs),
                        " | ".join(x.get("住所", "") for x in vs),
                        " | ".join(x.get("電話番号", "") for x in vs)])
    with open(os.path.join(OUT, "_検証_電話衝突.csv"), "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["電話", "件数", "店舗ID一覧", "店名一覧", "住所一覧"])
        for pk, vs in sorted(phone_conf.items(), key=lambda x: -len(x[1])):
            w.writerow([pk, len(vs),
                        " | ".join((x.get("店舗ID") or "").strip() for x in vs),
                        " | ".join(x.get("店舗名", "") for x in vs),
                        " | ".join(x.get("住所", "") for x in vs)])

    print("=" * 56)
    print(f"(norm_name,pref,city)重複: {len(dup_npc)} 種 / norm_name単体重複: {dup_name_only} 種")
    print(f"電話衝突（同一電話×複数店舗ID）: {len(phone_conf)} 件 → _検証_電話衝突.csv")
    print(f"出力: _output/_検証_重複キー.csv, _検証_電話衝突.csv")


if __name__ == "__main__":
    main()
