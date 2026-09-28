import os
import sys
import json
import csv
import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)
serp_key = os.getenv("SERPAPI_KEY", "")

items = [
    {"num": 1, "title": "60's Single Stitch Yellow Graphic Tshirt", "vendor": "Vintage", "cost": "N/A"},
    {"num": 2, "title": "Vintage Brown and Black Gianni Blouse", "vendor": "Vintage", "cost": "N/A"},
    {"num": 3, "title": "Vintage Black and White Pattern Button Down", "vendor": "Vintage", "cost": "N/A"},
    {"num": 4, "title": "Vintage Multi Pattern Button Down", "vendor": "Vintage", "cost": "N/A"},
    {"num": 5, "title": "Black and White Vintage Silk Flowy Top", "vendor": "Vintage", "cost": "N/A"},
    {"num": 6, "title": "Thierry Mugler Black Halter Neck Dress", "vendor": "Thierry Mugler", "cost": "$300"},
    {"num": 7, "title": "Vintage Pink and Tan Snakeskin Flowy Halter Neck Top", "vendor": "Vintage", "cost": "$30"},
    {"num": 8, "title": "Celine Blue Off The Sholder Blouse", "vendor": "Celine", "cost": "$115"},
    {"num": 9, "title": "D&G 1990s Black Wool Blend Trousers", "vendor": "Dolce and Gabbana", "cost": "$125"},
    {"num": 10, "title": "Vintage White and Cream Fringe Shawl", "vendor": "Vintage", "cost": "$30"},
]

def search_item(item):
    vendor = item['vendor'].strip()
    title = item['title'].strip()
    if vendor.lower() in title.lower():
        q = title
    else:
        q = f"{vendor} {title}"
    print(f"Searching item #{item['num']}: '{q}'")
    params = {
        "engine": "google",
        "q": q,
        "api_key": serp_key,
        "hl": "en",
        "gl": "us",
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=25)
        if resp.status_code == 200:
            return resp.json().get("organic_results", [])
    except Exception as e:
        print(f"Error searching {q}: {e}")
    return []

results_list = []
for item in items:
    org_results = search_item(item)
    
    qa_links = []
    for r in org_results:
        link = r.get("link", "")
        source = r.get("source") or r.get("displayed_link") or "Web"
        r_title = r.get("title", "")
        
        # Collect top links from major resale platforms or general web
        if any(domain in link.lower() for domain in ["grailed.com", "1stdibs.com", "therealreal.com", "vestiairecollective.com", "ebay.com", "etsy.com", "depop.com", "farfetch.com", "poshmark.com"]):
            if len(qa_links) < 2:
                qa_links.append({"source": source, "title": r_title, "link": link})
    
    if not qa_links and org_results:
        for r in org_results[:2]:
            qa_links.append({"source": r.get("source") or "Web", "title": r.get("title", ""), "link": r.get("link", "")})

    item["qa_links"] = qa_links
    item["top_results"] = org_results[:3]
    results_list.append(item)

with open("processed_10_results.json", "w", encoding="utf-8") as f:
    json.dump(results_list, f, indent=2, ensure_ascii=False)

print("Finished searching all 10 items!")
