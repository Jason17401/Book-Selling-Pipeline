"""Genre, Taiwanese condition grades and the 'to_check' status."""

from pipeline.core.config import Config
from pipeline.core.validate import CONDITION_ZH, CONDITIONS, refresh_status
from pipeline.photos.ingest import parse_caption
from pipeline.sources import genre, lookup, taiwan


def test_genre_from_paths_and_subjects():
    assert genre.from_parts(["首頁", "童書", "兒童文學／橋梁書", "冒險／驚悚小說"]) == "童書 > 冒險／驚悚小說"
    assert genre.from_parts(["博客來", "中文書", "心理勵志"]) == "心理勵志"
    assert genre.from_path("Juvenile Fiction / Action & Adventure / General") == "Juvenile Fiction > Action & Adventure"
    assert (
        genre.from_parts(["中文書", "童書", "神奇樹屋 44: 狄更斯的耶誕頌"], title="神奇樹屋 44: 狄更斯的耶誕頌")
        == "童書"
    )
    assert genre.from_subjects("兒童小說--美國; 冒險小說") == "兒童小說"


NCL_DETAIL = """<html><body><table>
<tr><th>書名</th><td>神奇樹屋. 44, 狄更斯的耶誕頌</td></tr>
<tr><th>作者</th><td>瑪麗.波.奧斯本著 ; 張毓如譯</td></tr>
<tr><th>出版機構</th><td>天下遠見</td></tr>
<tr><th>ISBN(裝訂方式)</th><td>9789862167557 (平裝)</td></tr>
<tr><th>主題標題</th><td>兒童小說; 冒險小說</td></tr>
<tr><th>定價</th><td>NT$250</td></tr>
<tr><th>出版年月</th><td>100/11</td></tr></table></body></html>"""


def test_ncl_record_gives_price_and_subject_heading_as_genre():
    d = taiwan.parse_ncl_detail(NCL_DETAIL)
    assert d["isbns"] == ["9789862167557"] and d["list_price"] == "250" and d["year"] == "2011"
    assert d["genre"] == "兒童小說" and d["title"].startswith("神奇樹屋")


def test_taiwanese_book_keeps_asking_for_a_chinese_genre(tmp_path):
    calls = []
    providers = {
        "google": lambda i, c: (
            calls.append("google")
            or {
                "title": "狄更斯的耶誕頌",
                "author": "瑪麗.波.奧斯本",
                "publisher": "天下遠見",
                "year": "2011",
                "genre": "Juvenile Fiction",
            }
        ),
        "ncl": lambda i, c: calls.append("ncl") or {"title": "神奇樹屋. 44, 狄更斯的耶誕頌", "genre": "兒童小說"},
        "eslite": lambda i, c: calls.append("eslite") or {"genre": "童書 > 冒險"},
    }
    cfg = Config(
        data_dir=tmp_path,
        providers_tw=("google", "ncl", "eslite"),
        stop_when=("title", "author", "publisher", "year", "genre"),
    )
    got = lookup.lookup_book("9789862167557", cfg, providers=providers)
    assert calls == ["google", "ncl"]  # google first; NCL for a Chinese genre; then enough
    assert got["title"] == "狄更斯的耶誕頌" and got["genre"] == "兒童小說"


def test_condition_grades_and_captions():
    assert CONDITIONS == ["new", "as_new", "good", "fair", "poor"]  # TAAZE's five grades, best first
    assert CONDITION_ZH["as_new"] == "近全新" and Config().default_condition == "as_new"  # second best
    assert parse_caption("近全新 150") == ("as_new", "150", "TWD")
    assert parse_caption("as new 90") == ("as_new", "90", "TWD") and parse_caption("like new")[0] == "as_new"
    from pipeline.core.validate import CONDITION_GRADES

    assert [g[3] for g in CONDITION_GRADES] == ["A", "B", "C", "D", "E"]  # TAAZE's letters
    assert "膠膜未拆" in CONDITION_GRADES[0][5] and "印刷褪色" in CONDITION_GRADES[4][5]
    assert parse_caption("良好120") == ("good", "120", "TWD")
    assert parse_caption("全新") == ("new", "", "")
    assert parse_caption("acceptable 5 NZD") == ("fair", "5", "NZD")
    r = {"condition": "acceptable"}
    refresh_status(r, Config())
    assert r["condition"] == "fair"  # old name upgraded


def test_complete_books_wait_for_a_quick_check(tmp_path):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    cfg = Config(data_dir=tmp_path)
    r = {
        "isbn13": "9789861371955",
        "title": "T",
        "author": "A",
        "condition": "as_new",
        "price": "120",
        "currency": "TWD",
        "back_photo": str(photo),
        "front_photo": str(photo),
    }
    assert refresh_status(r, cfg) == [] and r["status"] == "to_check"  # processed: complete, not yet checked
    refresh_status(r, cfg)
    assert r["status"] == "to_check"  # re-validating doesn't confirm it
    refresh_status(r, cfg, confirm=True)
    assert r["status"] == "validated"  # you saved it in the review window
    refresh_status(r, cfg)
    assert r["status"] == "validated"  # stays confirmed
    r["price"] = ""
    refresh_status(r, cfg)
    assert r["status"] == "enriched"  # something missing again


def test_older_condition_codes_are_converted(tmp_path, monkeypatch):
    from pipeline.core import store

    p = tmp_path / "books.csv"
    store.write_rows(p, [{"sku": "x-S01-01", "condition": "like_new"}, {"sku": "x-S01-02", "condition": "acceptable"}])
    assert [r["condition"] for r in store.read_rows(p)] == ["as_new", "fair"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEFAULT_CONDITION", "like_new")  # an older .env
    assert Config.from_env().default_condition == "as_new"


def test_caption_parsing():
    assert parse_caption("good 5") == ("good", "5", "TWD")
    assert parse_caption("good 5", "NZD") == ("good", "5", "NZD")
    assert parse_caption("like new 150 NTD", "NZD") == ("as_new", "150", "TWD")
    assert parse_caption("front") == ("", "", "")
    assert parse_caption("poor") == ("poor", "", "")
