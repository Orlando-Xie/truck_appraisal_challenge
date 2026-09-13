import json

from selectolax.parser import HTMLParser

html = open("data/_probe.html", encoding="utf-8").read()
tree = HTMLParser(html)

for s in tree.css('script[type="application/ld+json"]'):
    data = json.loads(s.text())
    if data.get("@type") != "ItemList":
        continue
    items = data["itemListElement"]
    print("ItemList entries:", len(items))
    p = items[0]["item"]
    print("keys:", list(p.keys()))
    print("images:", len(p.get("image", [])))
    print(json.dumps(p, indent=2, ensure_ascii=False)[:3000])
    print("\n=== image counts across all ===")
    print([len(i["item"].get("image", [])) for i in items])

print("\n=== card count via .sl-item ===")
print(len(tree.css(".sl-item")))
print("=== card count via .sales-list-item ===")
print(len(tree.css(".sales-list-item")))
print("=== unique data-code ===")
codes = [c.attributes.get("data-code") for c in tree.css(".sl-item[data-code]")]
print(len(codes), len(set(codes)))
