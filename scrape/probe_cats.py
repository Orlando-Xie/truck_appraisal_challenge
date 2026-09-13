"""Discover Autoline truck category ids and their ad counts."""

import re

import httpx
from selectolax.parser import HTMLParser

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

html = open("data/_probe.html", encoding="utf-8").read()
tree = HTMLParser(html)

seen: dict[str, str] = {}
for a in tree.css("a[href]"):
    href = a.attributes.get("href", "")
    m = re.search(r"--c(\d+)$", href)
    if m:
        text = " ".join((a.text() or "").split())[:60]
        seen.setdefault(href, text)

print("=== categories linked from the truck-tractor page ===")
for href, text in sorted(seen.items()):
    print(f"{text:45s} {href}")

# The site map page lists every category with counts.
for probe in ("https://autoline.info/-/trucks", "https://autoline.info/"):
    try:
        r = httpx.get(probe, headers=HEADERS, timeout=30, follow_redirects=True)
    except Exception as exc:
        print("\nFAILED", probe, exc)
        continue
    print(f"\n=== {probe} -> {r.status_code} ===")
    t2 = HTMLParser(r.text)
    out: dict[str, str] = {}
    for a in t2.css("a[href]"):
        href = a.attributes.get("href", "")
        if re.search(r"--c\d+$", href):
            out.setdefault(href, " ".join((a.text() or "").split())[:60])
    for href, text in sorted(out.items(), key=lambda kv: kv[1]):
        print(f"{text:45s} {href}")
