from crop_pipeline.crop import centered_fallback_box, compute_crop_box
from crop_pipeline.subject import SubjectBox


def test_crop_is_target_aspect():
    src = (1365, 2048)
    subject = SubjectBox(left=500, top=400, right=860, bottom=1700)
    box = compute_crop_box(src, subject, output_size=(1536, 2048), subject_height_fraction=0.90)
    w = box[2] - box[0]
    h = box[3] - box[1]
    # Allow off-by-one from rounding
    assert abs((w / h) - (1536 / 2048)) < 0.005


def test_crop_stays_in_source():
    src = (1365, 2048)
    # Subject pushed right against the edge of the frame.
    subject = SubjectBox(left=1100, top=400, right=1340, bottom=1700)
    box = compute_crop_box(src, subject, output_size=(1536, 2048), subject_height_fraction=0.90)
    assert box[0] >= 0
    assert box[1] >= 0
    assert box[2] <= src[0]
    assert box[3] <= src[1]


def test_centered_fallback_matches_aspect():
    box = centered_fallback_box((1365, 2048), (1536, 2048))
    w = box[2] - box[0]
    h = box[3] - box[1]
    assert abs((w / h) - (1536 / 2048)) < 0.005
