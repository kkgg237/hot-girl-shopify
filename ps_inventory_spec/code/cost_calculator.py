"""
Cost Calculator Module - Calculates landed all-in costs for inventory items
"""
from typing import Dict, List
from dataclasses import dataclass


@dataclass
class InventoryItem:
    """Represents a single inventory item with all cost components."""
    auction_id: str
    item_name: str
    item_name_translated: str
    quantity: int
    item_price_jpy: float
    domestic_shipping_jpy: float
    buyee_service_fee_jpy: float
    international_shipping_jpy: float  # Prorated
    customs_duty_jpy: float  # Prorated
    total_cost_jpy: float
    total_cost_usd: float
    unit_cost_usd: float


class CostCalculator:
    def __init__(self, exchange_rate: float = 0.0067):
        """
        Initialize cost calculator.

        Args:
            exchange_rate: JPY to USD exchange rate (default: 0.0067, ~150 JPY/USD)
        """
        self.exchange_rate = exchange_rate

    def calculate_landed_cost(self, invoice_data: Dict) -> List[InventoryItem]:
        """
        Calculate landed all-in cost for each item in the invoice.

        Args:
            invoice_data: Parsed invoice data with items and fees

        Returns:
            List of InventoryItem objects with calculated costs
        """
        items = []

        # Extract data from invoice
        item_list = invoice_data.get('items', [])
        total_items = len(item_list)

        if total_items == 0:
            return items

        # Get shared costs that need to be prorated
        international_shipping = invoice_data.get('international_shipping', 0)
        customs_duty = invoice_data.get('customs_duty', 0)
        total_domestic_shipping = invoice_data.get('total_domestic_shipping', 0)
        total_buyee_service_fee = invoice_data.get('buyee_service_fee', 0)

        # Calculate prorated amounts per item
        intl_shipping_per_item = international_shipping / total_items
        customs_per_item = customs_duty / total_items

        # Process each item
        for item_data in item_list:
            item_price = item_data.get('item_price', 0)
            quantity = item_data.get('quantity', 1)

            # Get item-specific fees
            domestic_shipping = item_data.get('domestic_shipping', 0)
            buyee_fee = item_data.get('buyee_fee', 0)

            # Calculate total cost in JPY
            total_jpy = (
                item_price +
                domestic_shipping +
                buyee_fee +
                intl_shipping_per_item +
                customs_per_item
            )

            # Convert to USD
            total_usd = total_jpy * self.exchange_rate
            unit_cost_usd = total_usd / quantity if quantity > 0 else total_usd

            # Create inventory item
            item = InventoryItem(
                auction_id=item_data.get('auction_id', ''),
                item_name=item_data.get('item_name', ''),
                item_name_translated=item_data.get('item_name_translated', ''),
                quantity=quantity,
                item_price_jpy=item_price,
                domestic_shipping_jpy=domestic_shipping,
                buyee_service_fee_jpy=buyee_fee,
                international_shipping_jpy=intl_shipping_per_item,
                customs_duty_jpy=customs_per_item,
                total_cost_jpy=total_jpy,
                total_cost_usd=total_usd,
                unit_cost_usd=unit_cost_usd
            )

            items.append(item)

        return items

    def calculate_from_buyee_invoice(self, parsed_data: Dict) -> List[InventoryItem]:
        """
        Calculate costs specifically for Buyee invoice format.

        Args:
            parsed_data: Parsed Buyee invoice data

        Returns:
            List of InventoryItem objects with calculated costs
        """
        # Based on the Test_Buyee.pdf structure:
        # - Item prices: individual item costs
        # - Domestic shipping: 16,820 JPY (from sellers to Buyee warehouse)
        # - Buyee Service Fee: 6,300 JPY (300 JPY per item for most items)
        # - International Shipping: 47,037 JPY
        # - Customs Duty: 15,506 JPY
        # - Grand Total: 190,251 JPY

        items = parsed_data.get('items', [])
        total_items = len(items)

        if total_items == 0:
            return []

        # Extract totals
        international_shipping = parsed_data.get('international_shipping_fee', 0)
        # If no international shipping found in PDF, assume $20 USD baseline (prorated across items)
        if international_shipping == 0:
            international_shipping = round(20 / self.exchange_rate)  # $20 USD → JPY
        customs_duty = parsed_data.get('customs_duty', 0)

        # Prorate shared costs
        intl_shipping_per_item = international_shipping / total_items
        customs_per_item = customs_duty / total_items

        inventory_items = []

        for item in items:
            item_price = item.get('item_price', 0)
            domestic_shipping = item.get('domestic_shipping_fee', 0)
            buyee_fee = item.get('buyee_service_fee', 0)
            quantity = item.get('quantity', 1)

            # Calculate total landed cost
            total_jpy = (
                item_price +
                domestic_shipping +
                buyee_fee +
                intl_shipping_per_item +
                customs_per_item
            )

            total_usd = total_jpy * self.exchange_rate
            unit_cost_usd = total_usd / quantity if quantity > 0 else total_usd

            inventory_item = InventoryItem(
                auction_id=item.get('auction_id', ''),
                item_name=item.get('item_name', ''),
                item_name_translated=item.get('item_name_translated', ''),
                quantity=quantity,
                item_price_jpy=item_price,
                domestic_shipping_jpy=domestic_shipping,
                buyee_service_fee_jpy=buyee_fee,
                international_shipping_jpy=intl_shipping_per_item,
                customs_duty_jpy=customs_per_item,
                total_cost_jpy=total_jpy,
                total_cost_usd=round(total_usd, 2),
                unit_cost_usd=round(unit_cost_usd, 2)
            )

            inventory_items.append(inventory_item)

        return inventory_items

    def calculate_from_brandstreet_invoice(self, parsed_data: Dict) -> List[InventoryItem]:
        """
        Calculate costs for BrandStreet invoice (prices already in USD).
        Intrinsic cost = item_price × 1.2 (fixed cost) × 1.15 (import tax).
        """
        inventory_items = []
        for item in parsed_data.get('items', []):
            price = item.get('item_price', 0)
            qty = item.get('quantity', 1)

            intrinsic_cost = price * 1.2 * 1.15
            unit_cost = intrinsic_cost / qty if qty > 0 else intrinsic_cost

            inventory_items.append(InventoryItem(
                auction_id=item.get('auction_id', ''),
                item_name=item.get('item_name', ''),
                item_name_translated=item.get('item_name_translated', ''),
                quantity=qty,
                item_price_jpy=0,
                domestic_shipping_jpy=0,
                buyee_service_fee_jpy=0,
                international_shipping_jpy=0,
                customs_duty_jpy=0,
                total_cost_jpy=0,
                total_cost_usd=round(intrinsic_cost, 2),
                unit_cost_usd=round(unit_cost, 2),
            ))

        return inventory_items

    def calculate_costs(self, invoice_data: dict, source: str) -> List[InventoryItem]:
        """Dispatch to the appropriate cost calculator based on invoice source."""
        if source == 'brandstreet':
            return self.calculate_from_brandstreet_invoice(invoice_data)
        elif source == 'buyee':
            return self.calculate_from_buyee_invoice(invoice_data)
        else:
            # Unknown source: treat as USD-priced (like BrandStreet)
            return self.calculate_from_brandstreet_invoice(invoice_data)

    def generate_summary(self, items: List[InventoryItem]) -> Dict:
        """
        Generate cost summary from inventory items.

        Args:
            items: List of InventoryItem objects

        Returns:
            Dictionary with summary statistics
        """
        if not items:
            return {}

        total_jpy = sum(item.total_cost_jpy for item in items)
        total_usd = sum(item.total_cost_usd for item in items)
        total_quantity = sum(item.quantity for item in items)
        avg_unit_cost = total_usd / total_quantity if total_quantity > 0 else 0

        return {
            'total_items': len(items),
            'total_quantity': total_quantity,
            'total_cost_jpy': round(total_jpy, 2),
            'total_cost_usd': round(total_usd, 2),
            'average_unit_cost_usd': round(avg_unit_cost, 2),
            'exchange_rate': self.exchange_rate
        }


if __name__ == "__main__":
    # Test the calculator
    calculator = CostCalculator(exchange_rate=0.0067)

    # Sample data based on Test_Buyee.pdf
    test_data = {
        'items': [
            {
                'auction_id': 'x1213701450',
                'item_name': 'RITSUKO SHIRAHAMA coat',
                'item_name_translated': 'High-grade fur coat',
                'quantity': 1,
                'item_price': 4730,
                'domestic_shipping_fee': 1000,
                'buyee_service_fee': 300
            },
            {
                'auction_id': 'u1213737714',
                'item_name': 'Grace Continental coat',
                'item_name_translated': 'Real mouton coat',
                'quantity': 1,
                'item_price': 7141,
                'domestic_shipping_fee': 1400,
                'buyee_service_fee': 300
            }
        ],
        'international_shipping_fee': 47037,
        'customs_duty': 15506
    }

    items = calculator.calculate_from_buyee_invoice(test_data)
    summary = calculator.generate_summary(items)

    print("=== Cost Summary ===")
    for key, value in summary.items():
        print(f"{key}: {value}")

    print("\n=== Item Details ===")
    for item in items:
        print(f"\n{item.auction_id}: {item.item_name_translated}")
        print(f"  Total Cost: ¥{item.total_cost_jpy:.2f} (${item.total_cost_usd:.2f})")
        print(f"  Unit Cost: ${item.unit_cost_usd:.2f}")
