import json

with open("cavalli_10_processed.json", "r", encoding="utf-8") as f:
    items = json.load(f)

for item in items:
    idx = item["index"]
    sku = item["sku"]
    brand = item["brand"]
    item_type = item["item_type"]
    size = item["size"]
    material = item["material"]
    notes = item["notes"]
    orig_desc = item["orig_desc"]
    euro_price = item["euro_price"]
    top_matches = item.get("top_matches", [])
    qa_links = item.get("qa_links", [])
    
    # Extract details from top matches to enrich title, color, pattern, and era
    match_titles = [m.get("title", "") for m in top_matches]
    match_titles_str = " ".join(match_titles).lower()
    
    # Determine era
    era = "2000s" # Default for Cavalli archive unless specified
    if "1990" in notes.lower() or "1990" in match_titles_str or "90s" in match_titles_str or "199" in match_titles_str:
        era = "1990s"
    elif "2000" in notes.lower() or "ss 2000" in notes.lower() or "s/s 2000" in notes.lower():
        era = "2000s"
    elif "2001" in notes.lower() or "2002" in notes.lower() or "2003" in notes.lower() or "2004" in notes.lower():
        era = "2000s"
        
    # Extract color / pattern hints from matches
    color_pattern = ""
    for kw in ["floral", "animal print", "snakeskin", "leopard", "dragon", "kamasutra", "mesh", "silk", "graphic print", "monogram", "lace", "sheer", "ruffle", "beaded", "tie-dye", "tiger"]:
        if kw in match_titles_str and kw not in color_pattern:
            if color_pattern:
                color_pattern += " " + kw
            else:
                color_pattern = kw
                
    if not color_pattern:
        color_pattern = material.lower().replace(" / viscose blend", "").replace("jersey", "").strip()

    # Formulate Shopify Title: Era + Brand + Color + Notable details or patterns + Type of garment
    # Rule: normalize Y2K -> 2000s
    base_garment = item_type.lower().replace(" / camisole top", " camisole top").replace(" / top", " top")
    
    title_parts = [era, brand]
    if color_pattern:
        title_parts.append(color_pattern)
    title_parts.append(base_garment)
    
    full_title = " ".join(title_parts).strip().lower()
    # Normalize Y2K -> 2000s
    full_title = full_title.replace("y2k era", "2000s").replace("y2k", "2000s")
    
    # Clean duplicate words
    words = full_title.split()
    clean_words = []
    seen = set()
    for w in words:
        if w not in seen:
            seen.add(w)
            clean_words.append(w)
    shopify_title = " ".join(clean_words)

    # Extract price range from matches
    prices = []
    for m in top_matches:
        p_dict = m.get("price") if isinstance(m.get("price"), dict) else {}
        val = p_dict.get("extracted_value") or p_dict.get("value")
        if val:
            prices.append(val)
            
    print(f"### Item {idx} [`{sku}`] — {brand}")
    print(f"* **Original Sheet Info:** `{orig_desc}` ({brand}) · Material: `{material}` · Cost: `€{euro_price}`")
    if notes:
        print(f"* **Sheet Notes:** 📌 `{notes}`")
    print(f"* **Generated Shopify Title:** `{shopify_title}`")
    print(f"* **Era & Runway Classification:** **{era}** {'(' + notes + ')' if notes else ''}")
    print(f"* **Top 1–2 Exact Visual Match QA Links:**")
    if qa_links:
        for q in qa_links[:2]:
            p_text = f" ({q['price']})" if q['price'] else ""
            print(f"  - **{q['source']}**{p_text}: [{q['title']}]({q['link']})")
    else:
        print("  - *No direct resale listing link*")
    print()
