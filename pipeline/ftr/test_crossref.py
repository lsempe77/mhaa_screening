import requests

# Test CrossRef with the 395 no-DOI titles at a lower threshold
r = requests.get("https://api.crossref.org/works",
                 params={"query.bibliographic": "Dietary interventions fibromyalgia systematic review", "rows": 3},
                 headers={"User-Agent": "lsempe@3ieimpact.org"}, timeout=20)
items = r.json().get("message", {}).get("items", [])
print(f"CrossRef results: {len(items)}")
for i in items[:3]:
    t = i.get("title", [""])[0]
    d = i.get("DOI", "")
    print(f"  {t[:60]} | {d}")

# Try another one
print()
r2 = requests.get("https://api.crossref.org/works",
                  params={"query.bibliographic": "Fear of childbirth depression anxiety pregnancy systematic review", "rows": 3},
                  headers={"User-Agent": "lsempe@3ieimpact.org"}, timeout=20)
items2 = r2.json().get("message", {}).get("items", [])
print(f"CrossRef results: {len(items2)}")
for i in items2[:3]:
    t = i.get("title", [""])[0]
    d = i.get("DOI", "")
    print(f"  {t[:60]} | {d}")
