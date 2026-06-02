"""利用ログの共通ヘルパー（イベントを Vercel KV の日次バケットへ追記）。

設計: _docs/設計_利用ログと分析.md（確定版）。
- 日次リスト `ev:YYYYMMDD` に LPUSH、EX 180日で自動失効。
- KV未設定/失敗時は黙ってスキップ（本体機能を止めない）。
"""
import os
import sys
import json
import random
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

try:
    import _kv
except Exception:
    _kv = None

SCHEMA_V = 1
MODEL_VERSION = "2026-06-02"   # スコア式の版（フェーズ06 momentum式）。式変更時に更新
TTL_SEC = 180 * 86400


def now_jst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)


def gen_id(prefix):
    """例 s_20260602_101500_4821。serverlessなので datetime/random 使用可。"""
    return f"{prefix}_{now_jst().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"


def log_event(d):
    """イベント辞書を日次バケットへ追記。共通フィールドを補完。失敗は無視。"""
    if not _kv or not _kv.is_configured():
        return
    try:
        d.setdefault("schema_v", SCHEMA_V)
        d.setdefault("model_version", MODEL_VERSION)
        d.setdefault("t", now_jst().isoformat(timespec="seconds"))
        key = "ev:" + now_jst().strftime("%Y%m%d")
        _kv._cmd("LPUSH", key, json.dumps(d, ensure_ascii=False))
        _kv._cmd("EXPIRE", key, TTL_SEC)
    except Exception:
        pass
