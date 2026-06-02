# -*- coding: utf-8 -*-
"""利用ログ分析：/api/log のエクスポートJSONを集計し、3目的のサマリーを出す。

事前にエクスポート（PowerShell例）:
  $h=@{Authorization="Bearer <LOG_TOKEN>"}
  Invoke-WebRequest "https://spc-route.vercel.app/api/log?days=90" -Headers $h `
    -OutFile "G:\...\訪問店舗提案サービス\_output\_log_export.json"

使い方:
  python _scripts/利用ログ分析.py            # _output/_log_export.json を読む
分析: ①本命度モデルの答え合わせ(3段ファネル) ②利用状況 ③運用の穴。
"""
import os
import sys
import csv
import json
import collections

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
SRC = os.path.join(BASE, "_output", "_log_export.json")

GOOD = {"興味あり", "掲載済"}
MISS = {"NG"}
OPS = {"不在", "閉店確認", "店舗確認できず", "対象外"}   # 運用/データ要因（成果分母から分離）


def mom_band(m):
    try:
        m = int(float(m))
    except (ValueError, TypeError):
        return "?"
    return "60+" if m >= 60 else "40-59" if m >= 40 else "0-39"


def main():
    if not os.path.exists(SRC):
        print(f"❌ {SRC} が無い。先に /api/log?days=N をBearerでエクスポートしてください。")
        sys.exit(1)
    data = json.load(open(SRC, encoding="utf-8"))
    events = data.get("events", data) if isinstance(data, dict) else data
    by = collections.Counter(e.get("ev") for e in events)
    searches = [e for e in events if e.get("ev") == "search"]
    routes = [e for e in events if e.get("ev") == "route"]
    visits = [e for e in events if e.get("ev") == "visit"]
    print(f"イベント総数 {len(events)}  内訳 {dict(by)}")

    # ② 利用状況
    print("\n=== ② 利用状況 ===")
    print("検索 エリア別:", dict(collections.Counter(s.get("area", "?") for s in searches).most_common(10)))
    print("検索 担当別:", dict(collections.Counter(s.get("owner", "?") for s in searches)))
    print("ルート発行 担当別:", dict(collections.Counter(r.get("owner", "?") for r in routes)))
    hours = collections.Counter((s.get("t", "")[11:13]) for s in searches if s.get("t"))
    print("検索 時間帯:", dict(sorted(hours.items())))

    # ③ 運用の穴
    print("\n=== ③ 運用の穴 ===")
    zero = [s for s in searches if (s.get("n", 1) == 0)]
    print(f"0件検索: {len(zero)} 件  エリア別:",
          dict(collections.Counter(s.get("area", "?") for s in zero).most_common(10)))
    print("使用半径の分布:", dict(collections.Counter(s.get("radius") for s in searches)))

    # ① 本命度モデルの答え合わせ（3段ファネル）
    print("\n=== ① 本命度モデルの答え合わせ ===")
    # route内構成（発行ルートのランク比率）
    rank_in_routes = collections.Counter()
    for r in routes:
        for st in r.get("stores", []):
            rank_in_routes[st.get("rank", "?")] += 1
    print("route内 ランク構成:", dict(rank_in_routes))
    # 成果率（visitのstatus×勢い帯/ランク・運用要因は分離）
    def funnel(group_fn):
        agg = collections.defaultdict(lambda: {"visit": 0, "good": 0, "miss": 0, "ops": 0})
        for v in visits:
            g = group_fn(v)
            a = agg[g]
            a["visit"] += 1
            st = v.get("status", "")
            if st in GOOD:
                a["good"] += 1
            elif st in MISS:
                a["miss"] += 1
            elif st in OPS:
                a["ops"] += 1
        return agg
    print("\n勢い帯別 訪問→成果（成果=興味あり+掲載済 / 分母は成果判定可のvisit）:")
    for band, a in sorted(funnel(lambda v: mom_band(v.get("mom_score_at_visit"))).items()):
        denom = a["good"] + a["miss"]
        rate = f"{100*a['good']//denom}%" if denom else "—"
        print(f"  勢い{band:6} 訪問{a['visit']:3} 成果{a['good']:3} NG{a['miss']:3} 運用要因{a['ops']:3} 成果率{rate}")
    print("ランク別 訪問→成果:")
    for rk, a in sorted(funnel(lambda v: v.get("rank_at_visit", "?")).items()):
        denom = a["good"] + a["miss"]
        rate = f"{100*a['good']//denom}%" if denom else "—"
        print(f"  {rk:4} 訪問{a['visit']:3} 成果{a['good']:3} NG{a['miss']:3} 運用要因{a['ops']:3} 成果率{rate}")
    # visit化率（route_idで連結できたvisitのみ母数に注記）
    linked = [v for v in visits if v.get("route_id")]
    print(f"\nroute連結ありvisit: {len(linked)} / 全visit {len(visits)}（visit化率は連結ありのみで算出可・非連結は成果分析のみ）")


if __name__ == "__main__":
    main()
