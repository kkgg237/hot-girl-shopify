import os
import sys
import json
import requests

def test_bing_visual_search(image_path: str, api_key: str):
    print(f"Testing Bing Visual Search API with file: {image_path}")
    endpoint = "https://api.bing.microsoft.com/v7.0/images/visualsearch"
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
    }
    
    with open(image_path, "rb") as f:
        files = {
            "image": (os.path.basename(image_path), f, "image/jpeg")
        }
        resp = requests.post(endpoint, headers=headers, files=files, timeout=30)
        
    print(f"HTTP Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error response: {resp.text}")
        return None
        
    data = resp.json()
    print("Tags count:", len(data.get("tags", [])))
    
    matches = []
    for tag in data.get("tags", []):
        for action in tag.get("actions", []):
            action_type = action.get("actionType")
            if action_type in ["VisualSearch", "PagesIncluding", "ProductVisualSearch", "ProductResults"]:
                data_items = action.get("data", {}).get("value", [])
                for item in data_items:
                    name = item.get("name") or item.get("title") or "Match"
                    url = item.get("hostPageUrl") or item.get("webSearchUrl") or ""
                    site = item.get("hostPageDisplayUrl") or ""
                    price = item.get("price") or item.get("offers", [{}])[0].get("price") or "N/A"
                    matches.append({"name": name, "url": url, "site": site, "price": price, "action": action_type})
                    
    print(f"\nFound {len(matches)} visual match pages:")
    for idx, m in enumerate(matches[:15], 1):
        print(f"[{idx}] {m['name']} ({m['site']})")
        print(f"     URL: {m['url']}")
        if m['price'] != "N/A":
            print(f"     Price: {m['price']}")
            
    return data

if __name__ == "__main__":
    key = os.getenv("BING_VISUAL_SEARCH_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    img_path = "/home/kat/workspace/hot-girl-shopify/japanese-invoice-transcriber/static/lens_cache/98957b2a6827913cf22a74528a2e1031.jpg"
    if not key:
        print("Usage: BING_VISUAL_SEARCH_KEY=your_key python test_bing_visual_search.py OR pass key as CLI arg")
        sys.exit(1)
    test_bing_visual_search(img_path, key)
