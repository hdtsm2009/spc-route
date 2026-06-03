"""既存KVプランの破棄スクリプト。

未エスケープHTMLが保存された旧プランを削除する。
実行前に KV_REST_API_URL / KV_REST_API_TOKEN を環境変数に設定すること。

  python _scripts/KVプランクリーンアップ.py [--dry-run]

--dry-run: 削除対象を表示するだけで実際には削除しない。
"""
import json
import os
import sys
import argparse
import urllib.request

_URL   = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL", "")
_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def _cmd(*args):
    """Upstash REST API に Redis コマンドを送る。"""
    if not _URL or not _TOKEN:
        raise RuntimeError("KV_REST_API_URL / KV_REST_API_TOKEN が未設定です")
    body = json.dumps(list(args)).encode("utf-8")
    req = urllib.request.Request(
        _URL,
        data=body,
        headers={"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["result"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="削除せず対象一覧を表示のみ")
    args = parser.parse_args()

    # 1. plans:list から plan ID を収集
    raw_list = _cmd("LRANGE", "plans:list", 0, 99)
    plan_ids = []
    for raw in (raw_list or []):
        try:
            plan_ids.append(json.loads(raw)["id"])
        except Exception:
            pass

    print(f"plans:list エントリ数: {len(plan_ids)}")
    for pid in plan_ids:
        print(f"  plan:{pid}")

    # 2. plan:* キーを個別スキャン（list に無いゴミキーも念のため）
    cursor = "0"
    orphan_keys = []
    while True:
        result = _cmd("SCAN", cursor, "MATCH", "plan:*", "COUNT", "100")
        cursor, keys = result[0], result[1]
        for k in keys:
            if k not in {f"plan:{pid}" for pid in plan_ids}:
                orphan_keys.append(k)
        if cursor == "0":
            break

    if orphan_keys:
        print(f"孤立 plan:* キー数: {len(orphan_keys)}")
        for k in orphan_keys:
            print(f"  {k}")

    if args.dry_run:
        print("\n--dry-run モード: 削除はスキップしました。")
        return

    # 3. 削除実行
    deleted = 0
    for pid in plan_ids:
        _cmd("DEL", f"plan:{pid}")
        deleted += 1
    for k in orphan_keys:
        _cmd("DEL", k)
        deleted += 1
    _cmd("DEL", "plans:list")
    print(f"\n削除完了: plan:* × {deleted} 件、plans:list をリセットしました。")


if __name__ == "__main__":
    main()
