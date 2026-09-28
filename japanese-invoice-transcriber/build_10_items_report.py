import json

with open("processed_10_results.json", "r", encoding="utf-8") as f:
    items = json.load(f)

formatted_output = []

for item in items:
    num = item["num"]
    orig_title = item["title"]
    vendor = item["vendor"].strip()
    cost = item.get("cost", "N/A")
    top_res = item.get("top_results", [])
    qa_links = item.get("qa_links", [])
    
    # Analyze title keywords for Era, Brand, Color, Details, Garment Type
    lower_t = orig_title.lower()
    
    # Era extraction
    era = ""
    if "60's" in lower_t or "60s" in lower_t or "1960s" in lower_t:
        era = "1960s"
    elif "80's" in lower_t or "80s" in lower_t or "1980s" in lower_t:
        era = "1980s"
    elif "90's" in lower_t or "90s" in lower_t or "1990s" in lower_t:
        era = "1990s"
    elif "y2k" in lower_t or "2000s" in lower_t or "00s" in lower_t:
        era = "2000s"
        
    # Brand/Vendor extraction
    brand = vendor if vendor.lower() != "vintage" else ""
    if not brand:
        if "thierry mugler" in lower_t or "mugler" in lower_t:
            brand = "Thierry Mugler"
        elif "celine" in lower_t:
            brand = "Celine"
        elif "dolce" in lower_t or "d&g" in lower_t:
            brand = "Dolce & Gabbana"
        elif "gianni" in lower_t:
            brand = "Gianni"
        elif "lolita lempicka" in lower_t:
            brand = "Lolita Lempicka"
        elif "ralph lauren" in lower_t:
            brand = "Ralph Lauren"
        elif "moschino" in lower_t:
            brand = "Moschino"
            
    # Formulate standardized Shopify Title: Era + Brand + Color + Details + Garment Type
    # Rule: normalize Y2K -> 2000s
    shopify_title = f"{era} {brand} {orig_title}".strip()
    # Clean duplicate words
    words = shopify_title.split()
    seen = set()
    cleaned_words = []
    for w in words:
        w_lower = w.lower()
        if w_lower in ["y2k", "y2k era"]:
            w = "2000s"
            w_lower = "2000s"
        if w_lower not in seen:
            seen.add(w_lower)
            cleaned_words.append(w)
    clean_shopify_title = " ".join(cleaned_words).lower()

    formatted_output.append({
        "num": num,
        "sheet_item": orig_title,
        "vendor": vendor,
        "cost": cost,
        "shopify_title": clean_shopify_title,
        "era": era or "Vintage / Undated",
        "brand": brand or "Vintage",
        "qa_links": qa_links,
        "top_res": top_res[:2]
    })

for item in formatted_output:
    print(f"### Item {item['num']}: {item['sheet_item']}")
    print(f"* **Vendor / Brand:** `{item['brand']}` (Cost: {item['cost']})")
    print(f"* **Generated Shopify Title:** `{item['shopify_title']}`")
    print(f"* **Era & Runway Notes:** {item['era']}")
    print("* **Top 1-2 QA Links & Current Comps:**")
    if item['qa_links']:
        for link in item['qa_links'][:2]:
            print(f"  - **{link['source']}**: [{link['title']}]({link['link']})")
    else:
        print("  - *No direct resale listing found*")
    print()
