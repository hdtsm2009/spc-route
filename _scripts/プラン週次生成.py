"""週次定型プラン自動生成スクリプト。

設定.json の presets を全件（または指定）読み込み、
各エリア・路線の訪問プランHTMLを一括生成する。

使い方:
  python _scripts/プラン週次生成.py                    # 全プリセット対話モード
  python _scripts/プラン週次生成.py --auto              # 全プリセットを自動生成（デフォルト設定）
  python _scripts/プラン週次生成.py --preset 西日暮里   # 名前部分一致で絞り込み
  python _scripts/プラン週次生成.py --owner 鈴村 --auto

生成されるファイル:
  _output/route_plans/週次プラン_<エリア名>_<日時>.html
"""
import os
import sys
import json
import datetime
import argparse

ROOT = r"G:\マイドライブ\作業フォルダ2025～\Claude作業フォルダ\Claudecode スポカフェ"
BASE = os.path.join(ROOT, "訪問店舗提案サービス")
SCRIPTS = os.path.join(BASE, "_scripts")
CONFIG_PATH = os.path.join(BASE, "_config", "設定.json")

# ルートプラン生成.py を同一ディレクトリからモジュールとしてロード
sys.path.insert(0, SCRIPTS)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "route_gen", os.path.join(SCRIPTS, "ルートプラン生成.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

load_master       = _mod.load_master
resolve_origin    = _mod.resolve_origin
filter_candidates = _mod.filter_candidates
build_free_route  = _mod.build_free_route
build_gmaps_buttons = _mod.build_gmaps_buttons
render_html       = _mod.render_html
MEMBERS           = _mod.MEMBERS
ROUTE_CFG         = _mod.ROUTE_CFG


def load_presets():
    with open(CONFIG_PATH, encoding="utf-8") as fp:
        cfg = json.load(fp)
    return cfg.get("presets", [])


def parse_args():
    p = argparse.ArgumentParser(description="週次定型プラン一括生成")
    p.add_argument("--preset",        default="",     help="プリセット名の部分一致でフィルタ（省略時=全件）")
    p.add_argument("--window_start",  default="17:00", help="活動開始時刻 HH:MM")
    p.add_argument("--window_end",    default="21:00", help="活動終了時刻 HH:MM")
    p.add_argument("--owner",         default="",     help="担当者名")
    p.add_argument("--max",           type=int, default=ROUTE_CFG["max_candidates"],
                   help="最大訪問件数")
    p.add_argument("--priority_n",    type=int, default=ROUTE_CFG["priority_n"],
                   help="メイン推奨件数（予備候補の境界）")
    p.add_argument("--return_to_origin", action="store_true", help="起点に戻るルートを生成")
    p.add_argument("--auto",          action="store_true", help="対話なしで自動生成")
    return p.parse_args()


def _ask(prompt: str, default: str = "", choices=None) -> str:
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  [{i}] {c}")
        print(f"  番号か名前を入力（デフォルト: {default or choices[0]}）: ", end="", flush=True)
        v = input().strip()
        if not v:
            return default or choices[0]
        try:
            idx = int(v) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        return v
    print(f"  {prompt}（デフォルト: {default}）: ", end="", flush=True)
    v = input().strip()
    return v if v else default


def generate_preset(preset: dict, rows: list, window_start: str, window_end: str,
                    owner: str, max_stores: int, priority_n: int,
                    return_to_origin: bool, now: str) -> str | None:
    """1プリセット分のHTMLを生成してパスを返す。失敗時はNone。"""
    name = preset["name"]
    lat = preset["lat"]
    lng = preset["lng"]
    radius = preset.get("radius_m", ROUTE_CFG["search_radius_m"])

    print(f"\n{'─'*50}")
    print(f"  エリア: {name}  ({lat}, {lng})")
    print(f"  時間帯: {window_start}〜{window_end}  担当: {owner or '未設定'}")

    origin = {
        "name": preset["station"],
        "addr": preset["station"],
        "lat": lat,
        "lng": lng,
        "type": "プリセット",
        "store_id": None,
    }

    cands = filter_candidates(rows, lat, lng, radius, window_start, window_end,
                              exclude_id=None)
    if not cands:
        print(f"  ⚠ {name}: 候補が0件のためスキップ")
        return None
    print(f"  候補: {len(cands)}件")

    plan, return_travel = build_free_route(
        cands, lat, lng, window_start, window_end, max_stores, return_to_origin)
    print(f"  プラン確定: {len(plan)}件")

    gmaps_buttons = build_gmaps_buttons(
        plan, [], origin["addr"], origin["addr"], mode="free",
        return_to_origin=return_to_origin)

    appt_info = {
        "name": origin["name"],
        "addr": origin["addr"],
        "window_start": window_start,
        "window_end": window_end,
        "radius": radius,
        "priority_n": priority_n,
        "return_to_origin": return_to_origin,
    }
    html = render_html(plan, 0, [], appt_info, owner or "—", now,
                       mode="free", origin=origin, gmaps_buttons=gmaps_buttons,
                       return_travel=return_travel)

    out_dir = os.path.join(BASE, "_output", "route_plans")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"週次プラン_{name}_{now}.html"
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(html)
    print(f"  ✅ 出力: {out_path}")
    return out_path


def main():
    args = parse_args()
    presets = load_presets()
    if not presets:
        print("❌ 設定.json に presets が定義されていません。")
        sys.exit(1)

    # フィルタ
    targets = presets
    if args.preset:
        targets = [p for p in presets if args.preset in p["name"]]
        if not targets:
            print(f"❌ '{args.preset}' に一致するプリセットがありません。")
            print("  登録済みプリセット:")
            for p in presets:
                print(f"    - {p['name']} ({p['station']})")
            sys.exit(1)

    print("=" * 60)
    print("  週次定型プラン一括生成")
    print("=" * 60)
    print(f"  対象プリセット: {len(targets)}件")
    for p in targets:
        print(f"    - {p['name']} ({p['station']})")

    # 共通パラメータ
    if args.auto:
        window_start  = args.window_start
        window_end    = args.window_end
        owner         = args.owner
    else:
        print()
        window_start = _ask("活動開始時刻", args.window_start)
        window_end   = _ask("活動終了時刻", args.window_end)
        print("  担当者名:")
        owner = _ask("担当者", args.owner or "", choices=MEMBERS)

    rows = load_master()
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M")

    generated = []
    for preset in targets:
        path = generate_preset(
            preset, rows,
            window_start, window_end, owner,
            args.max, args.priority_n,
            args.return_to_origin, now)
        if path:
            generated.append(path)

    # index.html を生成（全 route_plans の一覧）
    _generate_index(owner, window_start, window_end, now)

    print(f"\n{'='*60}")
    print(f"  完了: {len(generated)}/{len(targets)}件 生成")
    for p in generated:
        print(f"    {os.path.basename(p)}")
    print()


def _generate_index(owner: str, window_start: str, window_end: str, now: str):
    """_output/route_plans/ 内の全HTMLをスキャンして index.html を生成する。"""
    import re as _re
    out_dir = os.path.join(BASE, "_output", "route_plans")
    html_files = sorted(
        [f for f in os.listdir(out_dir) if f.endswith(".html") and f != "index.html"],
        reverse=True
    )

    # プリセット・メンバーを設定.jsonから取得
    presets = load_presets()
    with open(CONFIG_PATH, encoding="utf-8") as _f:
        _cfg = json.load(_f)
    members = _cfg.get("team", {}).get("members", ["鈴村", "鈴木"])

    preset_opts = "\n".join(
        f'<option value="{p["name"]}">{p["name"]} ({p["station"]})</option>'
        for p in presets
    )
    member_opts = "\n".join(
        f'<option value="{m}">{m}</option>' for m in members
    )

    def _label(name: str) -> str:
        return name.replace(".html", "").replace("_", " ")

    rows_html = ""
    for f in html_files:
        label = _label(f)
        m = _re.search(r"(\d{8}_\d{4})", f)
        dt_str = m.group(1) if m else ""
        dt_display = (f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]} "
                      f"{dt_str[9:11]}:{dt_str[11:13]}"
                      if len(dt_str) == 13 else "")
        is_weekly = f.startswith("週次プラン")
        tag = ('<span style="background:#e8f5e9;color:#2e7d32;font-size:10px;'
               'padding:1px 6px;border-radius:8px;border:1px solid #a5d6a7;margin-right:6px">'
               '週次</span>') if is_weekly else ""
        rows_html += f"""<tr>
  <td>{tag}<a href="{f}" target="_blank">{label}</a></td>
  <td style="color:#888;font-size:12px;white-space:nowrap">{dt_display}</td>
</tr>
"""

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>訪問プラン 一覧</title>
<style>
body{{font-family:'Hiragino Sans','Meiryo',sans-serif;font-size:14px;margin:0;color:#333;background:#f5f6fa}}
.page-wrap{{max-width:760px;margin:0 auto;padding:20px 16px}}
h1{{font-size:18px;margin-bottom:4px}}
.meta{{color:#888;font-size:12px;margin-bottom:20px}}
table{{border-collapse:collapse;width:100%}}
th{{background:#2c3e50;color:#fff;padding:7px 12px;text-align:left;font-size:13px}}
td{{padding:8px 12px;border-bottom:1px solid #eee;background:#fff}}
tr:hover td{{background:#f8f9fa}}
a{{color:#2980b9;text-decoration:none}}
a:hover{{text-decoration:underline}}
.footer{{margin-top:16px;color:#aaa;font-size:11px}}
/* 生成フォーム */
.gen-panel{{background:#fff;border:1px solid #dde;border-radius:8px;padding:18px 20px;
           margin-bottom:24px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.gen-panel h2{{font-size:15px;margin:0 0 14px;color:#2c3e50}}
.gen-row{{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-bottom:10px}}
.gen-field{{display:flex;flex-direction:column;gap:4px}}
.gen-field label{{font-size:11px;color:#666;font-weight:bold}}
.gen-field select,.gen-field input{{padding:7px 10px;border:1px solid #ccc;border-radius:4px;
  font-size:13px;background:#fff;min-width:120px}}
.gen-btn{{padding:9px 20px;background:#2c3e50;color:#fff;border:none;border-radius:4px;
          font-size:13px;cursor:pointer;white-space:nowrap}}
.gen-btn:hover{{background:#1a252f}}
.gen-btn:disabled{{background:#aaa;cursor:not-allowed}}
.gen-status{{font-size:12px;margin-top:8px;min-height:20px}}
.gen-status.ok{{color:#27ae60}}
.gen-status.err{{color:#e74c3c}}
@media(max-width:480px){{
  .gen-row{{flex-direction:column}}
  .gen-field select,.gen-field input{{min-width:unset;width:100%}}
  .gen-btn{{width:100%}}
}}
</style>
</head>
<body>
<div class="page-wrap">
<h1>📋 訪問プラン 一覧</h1>
<p class="meta">最終更新: {now[:4]}-{now[4:6]}-{now[6:8]} {now[9:11]}:{now[11:13]}</p>

<!-- ── プラン生成フォーム ── -->
<div class="gen-panel">
  <h2>🚀 プランをその場で生成</h2>
  <div class="gen-row">
    <div class="gen-field">
      <label>エリア</label>
      <select id="gen-area">
        <option value="">選択してください</option>
        {preset_opts}
      </select>
    </div>
    <div class="gen-field">
      <label>担当者</label>
      <select id="gen-owner">
        {member_opts}
      </select>
    </div>
    <div class="gen-field">
      <label>開始時刻</label>
      <input type="time" id="gen-start" value="17:00">
    </div>
    <div class="gen-field">
      <label>終了時刻</label>
      <input type="time" id="gen-end" value="21:00">
    </div>
    <button class="gen-btn" id="gen-btn" onclick="generatePlan()">🚀 生成</button>
  </div>
  <div class="gen-status" id="gen-status"></div>
</div>

<!-- ── 既存プラン一覧 ── -->
<table>
<thead><tr><th>プラン名</th><th>生成日時</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<p class="footer">一覧は <code>プラン週次生成.py</code> が自動更新します。</p>
</div>

<script>
async function generatePlan(){{
  const area  = document.getElementById('gen-area').value;
  const owner = document.getElementById('gen-owner').value;
  const start = document.getElementById('gen-start').value;
  const end   = document.getElementById('gen-end').value;
  const status = document.getElementById('gen-status');
  const btn    = document.getElementById('gen-btn');

  if(!area){{ status.className='gen-status err'; status.textContent='⚠ エリアを選択してください'; return; }}

  btn.disabled = true;
  btn.textContent = '⏳ 生成中...';
  status.className = 'gen-status';
  status.textContent = 'プランを生成しています（初回は10〜15秒かかる場合があります）...';

  try{{
    const res = await fetch('/.netlify/functions/generate_plan', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{area, owner, window_start: start, window_end: end}})
    }});
    if(!res.ok){{
      const msg = await res.text();
      throw new Error(msg);
    }}
    const html = await res.text();
    const blob = new Blob([html], {{type: 'text/html; charset=utf-8'}});
    const url  = URL.createObjectURL(blob);
    window.open(url, '_blank');
    status.className = 'gen-status ok';
    status.textContent = '✅ 生成完了！新しいタブで開きました。';
  }} catch(e){{
    status.className = 'gen-status err';
    status.textContent = '❌ エラー: ' + e.message;
  }} finally{{
    btn.disabled = false;
    btn.textContent = '🚀 生成';
  }}
}}
</script>
</body>
</html>"""

    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as fp:
        fp.write(html)
    print(f"  📋 index.html 更新: {index_path}")


if __name__ == "__main__":
    main()
