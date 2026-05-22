#!/usr/bin/env python3
"""
Process Buyee invoice PDF and generate Shopify-ready CSV
Usage:
  python process_invoice.py              # batch: process all PDFs in inputs/
  python process_invoice.py [path.pdf]   # single: process one specific PDF
"""
import sys
import os
import shutil

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdf_reader import PDFReader
from translator import Translator
from cost_calculator import CostCalculator
from data_formatter import DataFormatter
import re


def detect_lot_quantity(item_name: str) -> int:
    """
    Detect if an item listing is a lot/set and return the quantity.
    Checks Japanese and English patterns. Returns 1 if not a lot.
    """
    full_width = '０１２３４５６７８９'

    def fw_to_int(ch):
        return int(full_width.index(ch)) if ch in full_width else int(ch)

    # Japanese: 2点セット / ３点セット
    m = re.search(r'([２-９\d])点セット', item_name)
    if m:
        return fw_to_int(m.group(1))

    # Japanese: 2点まとめ
    m = re.search(r'([２-９\d])点まとめ', item_name)
    if m:
        return fw_to_int(m.group(1))

    # English: "set of 2", "2-piece set", "2 pieces", "2pcs"
    m = re.search(r'set of (\d+)', item_name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)[- ]?piece', item_name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*pcs', item_name, re.IGNORECASE)
    if m:
        return int(m.group(1))

    return 1


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUTS_DIR = os.path.join(BASE_DIR, 'inputs')
PROCESSED_DIR = os.path.join(INPUTS_DIR, 'processed')
PENDING_UPLOAD_DIR = os.path.join(BASE_DIR, 'outputs', 'pending_upload')
UPLOADED_DIR = os.path.join(BASE_DIR, 'outputs', 'uploaded')
INVENTORY_DIR = os.path.join(BASE_DIR, 'outputs', 'inventory')


def ensure_dirs():
    for d in [PROCESSED_DIR, PENDING_UPLOAD_DIR, UPLOADED_DIR, INVENTORY_DIR]:
        os.makedirs(d, exist_ok=True)


def detect_invoice_source(text: str) -> str:
    """Detect whether PDF is a Buyee or BrandStreet invoice."""
    if 'Brand Street' in text or 'brand street' in text.lower():
        return 'brandstreet'
    return 'buyee'


# ── Registry-based parser architecture ────────────────────────────────────────

class BaseInvoiceParser:
    source: str = 'unknown'

    def can_parse(self, text: str) -> bool:
        raise NotImplementedError

    def parse(self, text: str) -> dict:
        raise NotImplementedError


class BrandStreetParser(BaseInvoiceParser):
    source = 'brandstreet'

    def can_parse(self, text: str) -> bool:
        return 'Brand Street' in text or 'brand street' in text.lower()

    def parse(self, text: str) -> dict:
        return extract_brandstreet_invoice_data(text)


class BuyeeParser(BaseInvoiceParser):
    source = 'buyee'

    def can_parse(self, text: str) -> bool:
        return 'Buyee' in text or 'Shopping Site(ID)' in text

    def parse(self, text: str) -> dict:
        return extract_invoice_data_from_ocr(text)


# Ordered: more specific parsers first, catch-all last
INVOICE_PARSERS: list = [
    BrandStreetParser(),
    BuyeeParser(),
]


def detect_and_parse(text: str) -> dict:
    """Try each registered parser; fall back to Claude for unrecognized formats."""
    for parser in INVOICE_PARSERS:
        if parser.can_parse(text):
            data = parser.parse(text)
            data['source'] = parser.source
            return data
    return _parse_with_claude_fallback(text)


def _parse_with_claude_fallback(text: str) -> dict:
    """Use Claude Haiku to extract invoice data from an unrecognized format."""
    import anthropic
    import json

    client = anthropic.Anthropic()
    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=4096,
        messages=[{
            'role': 'user',
            'content': (
                'Extract all line items from this invoice. Return JSON only, no explanation.\n\n'
                'Use this exact structure:\n'
                '{\n'
                '  "source": "other",\n'
                '  "currency": "USD",\n'
                '  "invoice_date": "YYYY-MM-DD or blank",\n'
                '  "package_ref": "invoice or order number, or blank",\n'
                '  "international_shipping_fee": 0,\n'
                '  "customs_duty": 0,\n'
                '  "items": [\n'
                '    {"item_number": 1, "item_name": "full description", "quantity": 1, "item_price": 0.00}\n'
                '  ]\n'
                '}\n\n'
                'Rules:\n'
                '- Include EVERY line item — do not skip any\n'
                '- item_price is the unit price, not the line subtotal\n'
                '- If currency appears to be JPY, set "currency": "JPY"\n'
                '- Capture shipping/customs/fees in the top-level fields, not as items\n\n'
                f'Invoice text:\n{text}'
            ),
        }],
    )
    data = json.loads(response.content[0].text.strip())
    # Normalize item fields to match existing pipeline expectations
    for i, item in enumerate(data.get('items', [])):
        item.setdefault('auction_id', f"UNK{item.get('item_number', i + 1):03d}")
        item.setdefault('item_name_translated', item.get('item_name', ''))
        item.setdefault('domestic_shipping_fee', 0)
        item.setdefault('buyee_service_fee', 0)
    data.setdefault('delivery_date', data.get('invoice_date', ''))
    return data


def extract_brandstreet_invoice_data(text: str) -> dict:
    """Parse BrandStreet Tokyo invoice PDF to extract item data."""
    from datetime import datetime as _dt

    # Extract invoice metadata before stripping anything
    invoice_no = ''
    invoice_date = ''
    m = re.search(r'Invoice No#:\s*(\S+)', text)
    if m:
        invoice_no = m.group(1)
    m = re.search(r'Invoice Date:\s*([A-Za-z]+ \d+, \d+)', text)
    if m:
        try:
            invoice_date = _dt.strptime(m.group(1).strip(), '%b %d, %Y').strftime('%Y-%m-%d')
        except ValueError:
            invoice_date = m.group(1).strip()

    # Strip mid-page INVOICE header block (page-break footer: ends at AMOUNT DUE, not TOTAL)
    text_clean = re.sub(
        r'INVOICE\s+Invoice No#:.*?AMOUNT DUE',
        '', text, flags=re.DOTALL | re.IGNORECASE
    )
    # Strip end-of-page Subtotal/TOTAL block
    text_clean = re.sub(
        r'Subtotal\s+\$[\d,.]+\s+TOTAL\s+\$[\d,.]+ USD',
        '', text_clean, flags=re.DOTALL | re.IGNORECASE
    )

    # Tokenise
    skip_set = {
        '#', 'ITEMS & DESCRIPTION', 'QTY/HRS', 'PRICE', 'AMOUNT($)',
        'INVOICE', 'Subtotal', 'TOTAL', 'AMOUNT DUE', 'BILL TO',
        'Brand Street Tokyo',
    }
    lines = [l.strip() for l in text_clean.split('\n')]
    lines = [l for l in lines if l and l not in skip_set]

    items = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Item number: 1–999 integer
        if not re.match(r'^\d{1,3}$', line):
            i += 1
            continue

        item_num = int(line)

        # Collect description lines
        desc_parts = []
        j = i + 1
        while j < len(lines):
            cur = lines[j]

            # Stop at price
            if cur.startswith('$'):
                break

            # Stop at qty: small integer followed immediately by a price line
            if re.match(r'^\d{1,3}$', cur) and j + 1 < len(lines) and lines[j + 1].startswith('$'):
                break

            # Skip bare auth/model numbers (4+ digit lines)
            if re.match(r'^\d{4,}$', cur):
                j += 1
                continue

            # Skip single-character page artifacts
            if len(cur) <= 2:
                j += 1
                continue

            desc_parts.append(cur)
            j += 1

        # Qty
        qty = 1
        if j < len(lines) and re.match(r'^\d{1,2}$', lines[j]):
            qty = int(lines[j])
            j += 1

        # Price
        price = 0.0
        if j < len(lines) and lines[j].startswith('$'):
            pm = re.search(r'\$([\d,]+\.?\d*)', lines[j])
            if pm:
                price = float(pm.group(1).replace(',', ''))
            j += 1

        # Amount (same as price for qty=1 — skip)
        if j < len(lines) and lines[j].startswith('$'):
            j += 1

        if desc_parts and price > 0:
            raw = ' '.join(desc_parts)

            # Strip Auth codes: "Auth XXXXXX" or bare trailing "Auth"
            clean = re.sub(r'\bAuth\b(\s+\w+)?', '', raw, flags=re.IGNORECASE)
            # Strip model numbers (M51140, BA10345, ep12349, SW2008 …)
            clean = re.sub(r'\b[A-Za-z]{0,2}\d{4,}\b', '', clean)
            # Strip zero-padded reference codes (001, 007 …)
            clean = re.sub(r'\b0\d+\b', '', clean)
            # Strip standalone LV initials
            clean = re.sub(r'\bLV\b', '', clean)
            # Normalise whitespace
            clean = ' '.join(clean.split()).strip()

            items.append({
                'auction_id': f'BS{item_num:03d}',
                'item_name': clean,
                'item_name_translated': clean,   # already English
                'quantity': qty,
                'item_price': price,
                'domestic_shipping_fee': 0,
                'buyee_service_fee': 0,
            })

        i = j

    # Deduplicate by item number — page-break splits can produce the same item twice.
    # Keep the last occurrence (it tends to have the cleaner / more complete text).
    seen = {}
    for item in items:
        seen[item['auction_id']] = item
    items = [seen[k] for k in sorted(seen, key=lambda x: int(x[2:]))]

    package_ref = f'BS{invoice_no[-8:]}' if invoice_no else 'BS'

    return {
        'items': items,
        'invoice_no': invoice_no,
        'invoice_date': invoice_date,
        'delivery_date': invoice_date,
        'package_ref': package_ref,
        'international_shipping_fee': 0,
        'customs_duty': 0,
        'source': 'brandstreet',
    }


def extract_invoice_data_from_ocr(text: str) -> dict:
    """
    Parse text from Buyee invoice PDF to extract item data.

    Works with clean PyMuPDF-extracted text. The format is:
    - Shopping Site(ID) / JDirectItems Auction(auction_id)
    - Item Name / item description (may span multiple lines)
    - Quantity / 1
    - Item Price / price
    """
    lines = text.split('\n')

    # Extract invoice metadata
    delivery_date = ''
    package_ref = ''

    for i, line in enumerate(lines):
        if 'Package Reference No' in line:
            if i + 1 < len(lines):
                ref = lines[i + 1].strip()
                if re.match(r'^[A-Z]\d+', ref):
                    package_ref = ref
        if 'Date of Delivery' in line:
            match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
            if match:
                delivery_date = match.group(1)

    # Extract global totals
    international_shipping = 0
    customs_duty = 0

    for i, line in enumerate(lines):
        if 'International Shipping Fee' in line:
            # Next line has the fee
            if i + 1 < len(lines):
                match = re.search(r'([\d,]+)', lines[i + 1])
                if match:
                    international_shipping = float(match.group(1).replace(',', ''))

        if 'Customs Duty' in line and 'Tax' not in line:
            # Next line has the fee
            if i + 1 < len(lines):
                match = re.search(r'([\d,]+)', lines[i + 1])
                if match:
                    customs_duty = float(match.group(1).replace(',', ''))

    items_dict = {}
    domestic_shipping_fees = {}

    # Parse the main item listing section
    # Anchor on 'Shopping Site(ID)' which appears before every item regardless of shopping site
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line == 'Shopping Site(ID)':
            # Next line is the site+ID line in whatever format
            # e.g. "JDirectItems Auction(u1214956614)" or "LuxeWholesale(V26011400007)"
            if i + 1 >= len(lines):
                i += 1
                continue

            site_line = lines[i + 1].strip()
            id_match = re.search(r'\(([^)]+)\)$', site_line)
            if not id_match:
                i += 1
                continue

            item_id = id_match.group(1)

            if item_id in items_dict:
                i += 2
                continue

            item_name = ''
            item_price = 0
            j = i + 2

            while j < len(lines):
                next_line = lines[j].strip()

                if next_line == 'Item Name':
                    j += 1
                    name_lines = []
                    while j < len(lines) and lines[j].strip() != 'Quantity':
                        if lines[j].strip():
                            name_lines.append(lines[j].strip())
                        j += 1
                    item_name = ' '.join(name_lines)

                elif next_line == 'Item Price' and item_price == 0:
                    j += 1
                    if j < len(lines):
                        price_match = re.search(r'([\d,]+)', lines[j])
                        if price_match:
                            item_price = float(price_match.group(1).replace(',', ''))

                elif next_line == 'Shopping Site(ID)' or next_line == 'Other Service Fees':
                    break

                j += 1

            if item_name and item_price > 0:
                lot_qty = detect_lot_quantity(item_name)
                items_dict[item_id] = {
                    'auction_id': item_id,
                    'item_name': item_name,
                    'item_name_translated': '',
                    'quantity': lot_qty,
                    'item_price': item_price,
                    'domestic_shipping_fee': 900,  # Default, will update below
                    'buyee_service_fee': 300
                }

            i = j
            continue

        i += 1

    # Parse domestic shipping fees from the breakdown section
    in_domestic_shipping = False
    current_auction_id = None

    for i, line in enumerate(lines):
        line = line.strip()

        if 'Domestic Shipping Fee' in line and '15,150' not in line:
            in_domestic_shipping = True
            continue

        if in_domestic_shipping and 'Buyee Service Fee' in line:
            in_domestic_shipping = False
            continue

        if in_domestic_shipping:
            # Look for any item ID in parentheses (works for any shopping site format)
            auction_match = re.search(r'\(([^)]+)\)$', line)
            if auction_match:
                current_auction_id = auction_match.group(1)

            # Look for shipping fee (3-4 digit number on its own line)
            if current_auction_id and re.match(r'^[\d,]+$', line):
                fee = float(line.replace(',', ''))
                if 200 <= fee <= 2500:
                    if current_auction_id in items_dict:
                        items_dict[current_auction_id]['domestic_shipping_fee'] = fee
                    current_auction_id = None

    items = list(items_dict.values())

    return {
        'items': items,
        'international_shipping_fee': international_shipping,
        'customs_duty': customs_duty,
        'delivery_date': delivery_date,
        'package_ref': package_ref,
    }


def process_single_pdf(pdf_path: str, pdf_reader, translator, calculator, formatter) -> bool:
    """Process one PDF. Returns True on success, False on failure."""
    print(f"\n{'='*50}")
    print(f"Processing: {os.path.basename(pdf_path)}")
    print(f"{'='*50}")

    # Step 1: Extract text
    print("Step 1: Reading PDF...")
    try:
        raw_text = pdf_reader.extract_text_from_pdf(pdf_path, use_ocr=True)
        print(f"  Extracted {len(raw_text)} characters")
    except Exception as e:
        print(f"  Extraction failed: {e}")
        return False

    # Step 2: Parse invoice data
    print("\nStep 2: Parsing invoice data...")
    invoice_data = extract_invoice_data_from_ocr(raw_text)

    if not invoice_data['items']:
        print("  WARNING: Could not extract items from PDF.")
        debug_path = os.path.join(BASE_DIR, 'outputs', 'ocr_debug.txt')
        with open(debug_path, 'w', encoding='utf-8') as f:
            f.write(raw_text)
        print(f"  OCR text saved to: {debug_path}")
        return False

    package_ref = invoice_data.get('package_ref', '')
    delivery_date = invoice_data.get('delivery_date', '')
    print(f"  Found {len(invoice_data['items'])} items")
    print(f"  Package: {package_ref}  Delivery: {delivery_date}")
    print(f"  International Shipping: ¥{invoice_data['international_shipping_fee']:,.0f}")
    print(f"  Customs Duty: ¥{invoice_data['customs_duty']:,.0f}")

    # Step 3: Translate item names
    print("\nStep 3: Translating item names...")
    for i, item in enumerate(invoice_data['items'], 1):
        if item['item_name']:
            preview = item['item_name'][:40] + "..." if len(item['item_name']) > 40 else item['item_name']
            print(f"  [{i}/{len(invoice_data['items'])}] {preview}")
            item['item_name_translated'] = translator.translate_item_name(item['item_name'])
        else:
            item['item_name_translated'] = 'Unknown Item'

    # Step 4: Calculate costs
    print("\nStep 4: Calculating landed costs...")
    inventory_items = calculator.calculate_from_buyee_invoice(invoice_data)
    summary = calculator.generate_summary(inventory_items)
    print(f"  Total items: {summary['total_items']}")
    print(f"  Total cost: ¥{summary['total_cost_jpy']:,.0f} (${summary['total_cost_usd']:,.2f})")
    print(f"  Average unit cost: ${summary['average_unit_cost_usd']:.2f}")

    # Step 5: Generate outputs
    print("\nStep 5: Generating outputs...")

    # Inventory CSV → outputs/inventory/
    inv_filename = f"inventory_{package_ref}.csv" if package_ref else "inventory_output.csv"
    inv_path = os.path.join(INVENTORY_DIR, inv_filename)
    formatter.export_to_csv(inventory_items, inv_path)
    print(f"  Inventory CSV: outputs/inventory/{inv_filename}")

    # Shopify CSV → outputs/pending_upload/
    shopify_path = formatter.export_to_shopify_csv(
        inventory_items,
        output_dir=PENDING_UPLOAD_DIR,
        delivery_date=delivery_date,
        package_ref=package_ref,
    )
    print(f"  Shopify CSV:   outputs/pending_upload/{os.path.basename(shopify_path)}")

    # Step 6: Move PDF to inputs/processed/
    dest = os.path.join(PROCESSED_DIR, os.path.basename(pdf_path))
    shutil.move(pdf_path, dest)
    print(f"\n  Moved PDF → inputs/processed/{os.path.basename(pdf_path)}")

    print(f"\nSUCCESS: {os.path.basename(pdf_path)}")
    return True


def main():
    ensure_dirs()

    if len(sys.argv) > 1:
        # Single PDF mode
        pdf_paths = [sys.argv[1]]
    else:
        # Batch mode: process all PDFs in inputs/
        pdf_paths = sorted(
            [os.path.join(INPUTS_DIR, f) for f in os.listdir(INPUTS_DIR) if f.endswith('.pdf')]
        )
        if not pdf_paths:
            print("No PDFs found in inputs/. Add Buyee invoice PDFs and re-run.")
            sys.exit(1)
        print(f"Found {len(pdf_paths)} PDF(s) to process in inputs/")

    # Initialize shared modules once
    pdf_reader = PDFReader()
    translator = Translator()
    calculator = CostCalculator(exchange_rate=0.0067)
    formatter = DataFormatter()

    results = {'success': 0, 'failed': 0}
    for pdf_path in pdf_paths:
        if not os.path.exists(pdf_path):
            print(f"ERROR: Not found: {pdf_path}")
            results['failed'] += 1
            continue
        ok = process_single_pdf(pdf_path, pdf_reader, translator, calculator, formatter)
        if ok:
            results['success'] += 1
        else:
            results['failed'] += 1

    print(f"\n{'='*50}")
    print(f"DONE: {results['success']} processed, {results['failed']} failed")
    if results['success']:
        print(f"Shopify CSVs ready in: outputs/pending_upload/")
        print(f"After uploading, move them to: outputs/uploaded/")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
