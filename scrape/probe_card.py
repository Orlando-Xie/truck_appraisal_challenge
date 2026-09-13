"""Dump a single Autoline listing card so selectors can be written precisely."""

import re

from selectolax.parser import HTMLParser

html = open("data/_probe.html", encoding="utf-8").read()
tree = HTMLParser(html)

cards = tree.css("div.sales-list-item, li.sales-list-item, .sl-item")
print("cards found:", len(cards))
card = cards[0]
raw = card.html or ""
print("\n=== RAW CARD (first 9000 chars) ===")
print(raw[:9000])

print("\n\n=== ld+json blocks ===")
for s in tree.css('script[type="application/ld+json"]'):
    t = (s.text() or "").strip()
    print(t[:1500])
    print("---")

print("\n=== dataLayer / ecommerce ===")
for m in list(re.finditer(r"(dataLayer|ecommerce|impressions)\s*[=:]\s*", html))[:5]:
    print(html[m.start() : m.start() + 500].replace("\n", " ")[:500])
    print("---")
