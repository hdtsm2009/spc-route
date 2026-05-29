"""共通の正規化ユーティリティ（店名・住所・電話番号）。

名寄せ／突合のためのキー生成に使う。各ソースで表記揺れがあるため、
ここで統一ルールを定義する。
"""
import re
import unicodedata

# 店名から除去するノイズ（業態接頭・装飾・空白）
_NAME_NOISE = re.compile(
    r"(【[^】]*】|〈[^〉]*〉|\([^)]*\)|（[^）]*）|\[[^\]]*\])"
)
_SPACE = re.compile(r"\s+")


def to_halfwidth(s: str) -> str:
    """全角英数記号→半角、カナは全角のまま。"""
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s)).strip()


def norm_name(s: str) -> str:
    """名寄せ用の店名キー。装飾括弧・空白・記号を落として小文字化。"""
    if not s:
        return ""
    s = to_halfwidth(s)
    s = _NAME_NOISE.sub("", s)
    s = re.sub(r"[\s　・,，.。/／\-—–~〜!！?？「」『』]", "", s)
    return s.lower()


def norm_phone(s: str) -> str:
    """電話番号を数字のみに。最も信頼できる名寄せキー。"""
    if not s:
        return ""
    s = to_halfwidth(s)
    digits = re.sub(r"\D", "", s)
    # 先頭0始まりの9〜11桁を電話番号とみなす
    if 9 <= len(digits) <= 11:
        return digits
    return ""


# 〒・郵便番号・都道府県以降を整える
_POSTAL = re.compile(r"〒?\s*\d{3}\s*-?\s*\d{4}\s*")
_PREF = r"(北海道|東京都|京都府|大阪府|.{2,3}県)"


def norm_address(s: str) -> str:
    """ジオコーディング向けに住所を整形。郵便番号除去・全角半角統一・
    ビル名以降の余分なスペースを詰める。都道府県から始まる形に寄せる。"""
    if not s:
        return ""
    s = to_halfwidth(s)
    s = _POSTAL.sub("", s)
    s = _SPACE.sub(" ", s).strip()
    # 都道府県が途中から始まる場合はそこから切る
    m = re.search(_PREF, s)
    if m and m.start() > 0:
        s = s[m.start():]
    return s


def extract_pref_city(addr: str):
    """住所から (都道府県, 市区町村) をざっくり抽出。突合の補助キー。"""
    a = norm_address(addr)
    pref = ""
    city = ""
    m = re.match(_PREF, a)
    if m:
        pref = m.group(1)
        rest = a[m.end():]
        m2 = re.match(r"(.+?[市区町村])", rest)
        if m2:
            city = m2.group(1)
    return pref, city
