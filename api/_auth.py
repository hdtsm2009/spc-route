"""共有認証ユーティリティ。

全 API ハンドラが import して使う。
ブラウザ向けは Vercel Authentication / Deployment Protection で保護すること。
このトークンは curl・スクリプト等 API レベルの制御用。
"""
import os


def check_token(headers) -> bool:
    """Bearer トークン認証（書き込み・機密系エンドポイント用）。

    - API_TOKEN 設定あり       : Authorization: Bearer <token> が一致すれば許可
    - API_TOKEN 未設定 + production : fail-closed（401）
    - API_TOKEN 未設定 + それ以外   : 全通し（dev / preview / local）
    """
    token = os.environ.get("API_TOKEN", "")
    if token:
        auth = headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:] == token
    if os.environ.get("VERCEL_ENV") == "production":
        return False
    return True


def check_token_browser(headers) -> bool:
    """読み取り系・ブラウザ向けエンドポイント用。

    Vercel Authentication が有効になるまでの暫定として全通し。
    candidates / generate_plan に使用。
    visit POST / plans GET は check_token（厳格側）を使うこと。
    """
    return True


AUTH_401 = {"error": "認証が必要です（Authorization: Bearer <token>）"}
