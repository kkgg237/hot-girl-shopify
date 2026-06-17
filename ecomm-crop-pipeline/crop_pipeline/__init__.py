"""ecomm-crop-pipeline — turn Capture One studio exports into clean ecomm crops.

Public API:
    from crop_pipeline import process_image, process_folder, CropOptions

The library detects the model in each studio photo, crops to a target aspect
ratio centered on the subject, removes the background, and composites onto
pure white. Designed to be importable as tooling by other tools.
"""

from crop_pipeline.options import CropOptions
from crop_pipeline.pipeline import process_image, process_folder, process_with_template
from crop_pipeline.templates import Template, ShotSpec, load_template

__all__ = [
    "CropOptions",
    "process_image",
    "process_folder",
    "process_with_template",
    "Template",
    "ShotSpec",
    "load_template",
]
