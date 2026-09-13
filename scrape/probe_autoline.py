"""Throwaway probe: dump Autoline's markup so selectors can be written against
the real DOM rather than guessed."""

import re
import sys

import httpx
from selectolax.parser import HTMLParser

URL = sys.argv[1] if len(sys.argv) > 1 else "https://autoline.info/-/truck-tractors--c42"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

r = httpx.get(URL, headers=HEADERS, timeout=30, follow_redirects=True)
print("STATUS", r.status_code, "LEN", len(r.text))
html = r.text
open("data/_probe.html", "w", encoding="utf-8").write(html)

tree = HTMLParser(html)

# Which class names look like listing cards?
counts: dict[str, int] = {}
for node in tree.css("*[class]"):
    for cls in (node.attributes.get("class") or "").split():
        counts[cls] = counts.get(cls, 0) + 1
print("\n=== most common classes ===")
for cls, n in sorted(counts.items(), key=lambda kv: -kv[1])[:60]:
    print(f"{n:5d}  {cls}")

print("\n=== sale links ===")
links = [a.attributes.get("href", "") for a in tree.css("a")]
sale = [h for h in links if "/sale/" in h or "/auction/" in h]
print("count:", len(sale))
for h in sale[:5]:
    print(" ", h)

print("\n=== any data attributes ===")
attrs: dict[str, int] = {}
for node in tree.css("*"):
    for k in node.attributes:
        if k.startswith("data-"):
            attrs[k] = attrs.get(k, 0) + 1
for k, n in sorted(attrs.items(), key=lambda kv: -kv[1])[:30]:
    print(f"{n:5d}  {k}")

print("\n=== price-looking text ===")
for m in list(re.finditer(r"[€$]\s?[\d ,.]{3,}", html))[:10]:
    print(" ", m.group(0)[:40])

print("\n=== embedded JSON blobs ===")
for m in list(re.finditer(r'<script[^>]*type="application/(ld\+)?json"[^>]*>', html))[:10]:
    print(" ", m.group(0)[:120])
