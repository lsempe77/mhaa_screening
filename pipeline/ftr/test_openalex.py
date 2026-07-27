import requests

# Try different search approaches
title = "Dietary interventions in fibromyalgia"

# Method 1: title.search filter
r1 = requests.get("https://api.openalex.org/works",
                  params={"filter": f"title.search:{title}", "per_page": 3},
                  headers={"User-Agent": "lsempe@3ieimpact.org"}, timeout=20)
print(f"title.search: {len(r1.json().get('results', []))} results")

# Method 2: search parameter (full text)
r2 = requests.get("https://api.openalex.org/works",
                  params={"search": title, "per_page": 3},
                  headers={"User-Agent": "lsempe@3ieimpact.org"}, timeout=20)
res2 = r2.json().get("results", [])
print(f"search: {len(res2)} results")
for w in res2[:3]:
    print(f"  {w.get('title','')} | {w.get('doi','')} | {w.get('publication_year','')}")

# Method 3: try a different title
title2 = "Fear of childbirth depression anxiety during pregnancy"
r3 = requests.get("https://api.openalex.org/works",
                  params={"search": title2, "per_page": 3},
                  headers={"User-Agent": "lsempe@3ieimpact.org"}, timeout=20)
res3 = r3.json().get("results", [])
print(f"\nsearch '{title2}': {len(res3)} results")
for w in res3[:3]:
    print(f"  {w.get('title','')} | {w.get('doi','')} | {w.get('publication_year','')}")

