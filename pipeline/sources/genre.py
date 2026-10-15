"""The book's genre, from whatever a source offers: a category path (eslite levels, a books.com.tw breadcrumb, Google's
'Juvenile Fiction / Action & Adventure / General') or a library subject heading (NCL 主題標題).

Stored as "<overarching> > <most specific>", e.g. "童書 > 冒險／驚悚小說" or "Juvenile Fiction > Action & Adventure":
the first level says what kind of book it is (good for shop sections), the last one what exactly. Navigation steps
that are not genres are dropped: 首頁, 博客來, 中文書, 外文書, 電子書, 二手書, 全站分類, "General" ..."""
from __future__ import annotations

import re

SEP = " > "         # how genres are joined in books.csv: broadest first, e.g. "童書 > 冒險／驚悚小說"
# What separates genres in text from a source or typed by hand: > (and look-alikes), | and ; - but never "/" or "／"
# (books.com.tw and eslite use them INSIDE one category name, e.g. 冒險／驚悚小說), never "," ("Body, Mind & Spirit"
# is one Google category) and never a space (many genres are several words).
SPLIT = r"\s*(?:>|＞|›|»|\||｜|;|；)\s*"

NOT_GENRE = {"首頁", "home", "博客來", "誠品線上", "誠品", "全站分類", "中文書", "繁體書", "簡體書", "外文書", "電子書",
             "二手書", "書籍", "圖書", "book", "books", "general", "全部", "全部分類", "商品分類", "分類", "其他",
             "本書分類", "購物車", "中文出版品"}


def clean_parts(parts) -> list:
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", str(p or "")).strip(" >/|›»")
        if not p or p.lower() in NOT_GENRE or p in NOT_GENRE or len(p) > 40:
            continue
        if p not in out:
            out.append(p)
    return out


def from_parts(parts, title: str = "") -> str:
    """['首頁', '童書', '兒童文學／橋梁書', '冒險／驚悚小說'] -> '童書 > 冒險／驚悚小說'.
    A last step that is the book itself (its title) is not a genre and is dropped."""
    parts = clean_parts(parts)
    if title:
        t = re.sub(r"\W+", "", title).lower()
        parts = [p for p in parts if not (len(re.sub(r"\W+", "", p)) > 3 and re.sub(r"\W+", "", p).lower() in t)]
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else join_genres([parts[0], parts[-1]])


def from_path(text: str, sep: str = r"\s*(?:/|>|›|»|＞)\s*", title: str = "") -> str:
    """'Juvenile Fiction / Action & Adventure / General' -> 'Juvenile Fiction > Action & Adventure'."""
    return from_parts(re.split(sep, text or ""), title)


def from_subjects(text: str) -> str:
    """NCL 主題標題 / Subject Heading, e.g. '兒童小說; 美國文學' or '兒童小說--美國': the first heading, its main term."""
    first = re.split(r"\s*[;；、\n]\s*", (text or "").strip())[0]
    first = re.split(r"\s*(?:--|－－|—)\s*", first)[0]
    first = re.sub(r"^\s*\d+[.)]\s*", "", first)          # '1. 兒童小說'
    return first.strip(" .;")


def split_genres(text: str) -> list:
    """'童書 > 冒險／驚悚小說' -> ['童書', '冒險／驚悚小說'] (each genre once, empty bits dropped)."""
    out = []
    for g in re.split(SPLIT, text or ""):
        g = re.sub(r"\s+", " ", g).strip()
        if g and g not in out:
            out.append(g)
    return out


def join_genres(genres) -> str:
    """['童書', '冒險／驚悚小說'] -> '童書 > 冒險／驚悚小說' (the way books.csv stores them)."""
    return SEP.join(split_genres(SEP.join(str(g) for g in genres)))
