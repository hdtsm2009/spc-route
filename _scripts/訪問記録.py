"""訪問結果を記録する。

_feedback/visit_results.csv に1行追記し、
統合店舗マスタ_v2.csv の sales列 を更新する。

使い方（対話モード）:
  python 訪問記録.py

引数モード（HTML の 📝 ボタンから呼び出す想定）:
  python 訪問記録.py --store_id S1A2B3C4 --owner 鈴村
"""
import os
import csv
import json
import sys
import io
import argparse
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
CONFIG_PATH = os.path.join(BASE, "_config", "設定.json")
MASTER_V2 = os.path.join(BASE, "_output", "統合店舗マスタ_v2.csv")
FEEDBACK_DIR = os.path.join(BASE, "_feedback")
VISIT_LOG = os.path.join(FEEDBACK_DIR, "visit_results.csv")

with open(CONFIG_PATH, encoding="utf-8") as fp:
    CFG = json.load(fp)

MEMBERS = CFG["team"]["members"]
STATUS_OPTIONS = CFG["sales_status_options"]
CONTACT_OPTIONS = CFG["contact_method_options"]
NEXT_OPTIONS = CFG["next_action_options"]

VISIT_COLS = [
    "visit_id", "store_id", "店名", "住所", "訪問日",
    "owner", "contact_method", "result", "next_action", "memo",
    "掲載見込み",
]


def ask(prompt, options=None, default=""):
    if options:
        print(f"  選択肢: {' / '.join(options)}")
    hint = f"[{default}]" if default else ""
    val = input(f"{prompt} {hint}: ").strip()
    return val if val else default


def load_master():
    if not os.path.exists(MASTER_V2):
        return [], []
    with open(MASTER_V2, encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        rows = list(reader)
        fieldnames = reader.fieldnames
    return rows, fieldnames


def find_store(rows, store_id):
    for r in rows:
        if r.get("店舗ID") == store_id:
            return r
    return None


def next_visit_id():
    os.makedirs(FEEDBACK_DIR, exist_ok=True)
    if not os.path.exists(VISIT_LOG):
        return "V00001"
    with open(VISIT_LOG, encoding="utf-8-sig", newline="") as fp:
        n = sum(1 for _ in csv.reader(fp)) - 1  # ヘッダ除く
    return f"V{n + 1:05d}"


def append_visit_log(entry: dict):
    os.makedirs(FEEDBACK_DIR, exist_ok=True)
    write_header = not os.path.exists(VISIT_LOG)
    with open(VISIT_LOG, "a", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=VISIT_COLS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(entry)


def update_master(rows, fieldnames, store_id, sales_status, last_contact_date,
                  contact_method, result, next_action, owner, memo):
    for r in rows:
        if r.get("店舗ID") == store_id:
            r["sales_status"] = sales_status
            r["last_contact_date"] = last_contact_date
            r["contact_method"] = contact_method
            r["result"] = result
            r["next_action"] = next_action
            r["owner"] = owner
            if memo:
                existing = r.get("memo", "")
                r["memo"] = f"{memo}" if not existing else f"{existing} / {memo}"
    with open(MASTER_V2, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--store_id", default="")
    p.add_argument("--owner", default="")
    return p.parse_args()


def main():
    args = parse_args()
    rows, fieldnames = load_master()

    print("=" * 60)
    print("  訪問記録入力")
    print("=" * 60)

    # 店舗ID特定
    store_id = args.store_id
    if not store_id:
        store_id = ask("店舗ID（HTMLの店舗IDコラムから）")

    store = find_store(rows, store_id)
    if store:
        print(f"\n  店舗: {store.get('店名')} / {store.get('住所', '')[:30]}")
    else:
        print(f"\n  ⚠ 店舗ID '{store_id}' が見つかりません。店名で確認してください。")

    # 担当者
    owner = args.owner or ask("担当者名", options=MEMBERS)

    # 訪問日
    today = datetime.date.today().isoformat()
    visit_date = ask("訪問日", default=today)

    # 接触方法
    contact_method = ask("接触方法", options=CONTACT_OPTIONS)

    # 接触結果
    result_options = ["名刺獲得", "担当不在", "興味あり", "前向き", "NG", "再訪予定", "その他"]
    result = ask("接触結果", options=result_options)

    # sales_status 自動判定（resultから推定）
    status_map = {
        "名刺獲得": "訪問済", "担当不在": "訪問済", "興味あり": "興味あり",
        "前向き": "興味あり", "NG": "NG", "再訪予定": "再訪候補",
    }
    sales_status = status_map.get(result, "訪問済")
    print(f"  → sales_status: {sales_status}")

    # 次回アクション
    next_action = ask("次回アクション", options=NEXT_OPTIONS)

    # 掲載見込み
    outlook = ask("掲載見込み", options=["高", "中", "低", "なし"])

    # メモ
    memo = ask("営業メモ（自由記述、省略可）")

    # --- 記録 ---
    vid = next_visit_id()
    entry = {
        "visit_id": vid,
        "store_id": store_id,
        "店名": store.get("店名", "") if store else "",
        "住所": store.get("住所", "") if store else "",
        "訪問日": visit_date,
        "owner": owner,
        "contact_method": contact_method,
        "result": result,
        "next_action": next_action,
        "memo": memo,
        "掲載見込み": outlook,
    }
    append_visit_log(entry)
    print(f"\n✅ 訪問ログ記録: {VISIT_LOG}  (ID: {vid})")

    if rows and fieldnames:
        update_master(rows, fieldnames, store_id, sales_status, visit_date,
                      contact_method, result, next_action, owner, memo)
        print(f"✅ マスタ更新: {MASTER_V2}")
        print(f"   店舗ID {store_id} → sales_status={sales_status}")
    else:
        print("⚠ マスタが見つからないためマスタ更新をスキップしました。")


if __name__ == "__main__":
    main()
