"""Putting a product's listing photos online on ImgBB (imgbb.com), so the upload file can carry their links and
Shopee fetches them itself - no dragging photos into each product by hand.

Set IMGBB_API_KEY in .env (free account, no card: imgbb.com > sign up > api.imgbb.com > Get API key). Each photo gets
a permanent direct link (https://i.ibb.co/...). Without a key no links are made: you add the photos by hand in
Seller Center.

Every photo is uploaded once: its link is kept in <photos folder>/<product code>/links.json together with a
fingerprint of the picture, and reused until the picture changes (so a new upload file does not upload everything
again). The API key goes in the request body - never in a link.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from ..core.config import Config

IMGBB_URL = "https://api.imgbb.com/1/upload"


class PhotoHostError(RuntimeError):
    """The photos could not be put online (shown as one plain message)."""


def _fingerprint(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def _load_links(folder: Path) -> dict:
    try:
        return json.loads((folder / "links.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_links(folder: Path, links: dict) -> None:
    (folder / "links.json").write_text(json.dumps(links, indent=1, ensure_ascii=False), encoding="utf-8")


def _imgbb(cfg: Config, path: Path, name: str, http=None) -> str:
    """One photo to ImgBB; returns its direct link. The key travels in the POST body, not the link."""
    if not cfg.imgbb_api_key:
        raise PhotoHostError(
            "photos online need IMGBB_API_KEY in .env (free: imgbb.com > sign up > api.imgbb.com > Get API key)"
        )
    if http is None:
        import requests

        http = requests
    data = {"key": cfg.imgbb_api_key, "name": name, "image": base64.b64encode(Path(path).read_bytes()).decode("ascii")}
    try:
        r = http.post(IMGBB_URL, data=data, timeout=120)
    except Exception as exc:
        raise PhotoHostError(
            f"ImgBB could not be reached ({type(exc).__name__}) - check the internet connection"
        ) from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("success"):
        why = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        raise PhotoHostError(
            f"ImgBB refused {Path(path).name} (HTTP {r.status_code}"
            + (f": {why}" if why else "")
            + ") - check IMGBB_API_KEY in .env"
        )
    url = (body.get("data") or {}).get("url") or ""
    if not url.startswith("https://"):
        raise PhotoHostError(f"ImgBB gave no link for {Path(path).name}")
    return url


def put_online(cfg: Config, code: str, paths: list, http=None) -> list:
    """The photos' links on ImgBB, in order ([] without IMGBB_API_KEY). A photo already online with the same picture
    keeps its link."""
    service = cfg.photo_service
    if not service or not paths:
        return []
    folder = Path(paths[0]).parent
    links = _load_links(folder)
    out = []
    try:
        for p in paths:
            p = Path(p)
            fp = _fingerprint(p)
            known = links.get(p.name) or {}
            if known.get("sha1") == fp and known.get("host") == service and known.get("url"):
                out.append(known["url"])
                continue
            url = _imgbb(cfg, p, f"{code}-{p.stem}", http)
            links[p.name] = {"url": url, "sha1": fp, "host": service}
            out.append(url)
    finally:
        _save_links(folder, links)  # what did get online is remembered even if a later photo failed
    return out
