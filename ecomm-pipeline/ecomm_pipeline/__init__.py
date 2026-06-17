"""ecomm-pipeline — orchestrate Capture One export → crop pipeline → Shopify draft photo upload.

The photo lane of the one-of-one vintage store. This package never creates or
publishes products; it attaches cropped photos to *existing* draft listings,
matched by Variant SKU, and tags them complete for human review.

See README.md for the design and build phases.
"""

__version__ = "0.1.0"
