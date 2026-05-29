"""Upstash Redis (Vercel KV) REST APIヘルパー。

Vercel上で環境変数が設定されていれば動作。未設定なら RuntimeError。
呼び出し側は try/except でフォールバックすること。

対応する環境変数（どちらの命名でも可）:
  KV_REST_API_URL / KV_REST_API_TOKEN          （Vercel KV 旧命名）
  UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN （Upstash 標準命名）
"""
import os
import json
import urllib.request

_URL = (os.environ.get("KV_REST_API_URL")
        or os.environ.get("UPSTASH_REDIS_REST_URL")
        or "")
_TOKEN = (os.environ.get("KV_REST_API_TOKEN")
          or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
          or "")


def is_configured() -> bool:
    return bool(_URL and _TOKEN)


def _cmd(*args):
    if not is_configured():
        raise RuntimeError("KV未設定（KV_REST_API_URL / KV_REST_API_TOKEN）")
    req = urllib.request.Request(
        _URL,
        data=json.dumps([str(a) for a in args]).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("result")


def kv_set(key, value, ex=None):
    if ex:
        return _cmd("SET", key, value, "EX", ex)
    return _cmd("SET", key, value)


def kv_get(key):
    return _cmd("GET", key)


def kv_lpush(key, value):
    return _cmd("LPUSH", key, value)


def kv_ltrim(key, start, stop):
    return _cmd("LTRIM", key, start, stop)


def kv_lrange(key, start, stop):
    return _cmd("LRANGE", key, start, stop) or []
