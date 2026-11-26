"""'Masked area with context' inpainting helpers.

SDXL paints best at ~1024px. A small mask on a large image is therefore cropped (with
surrounding context), scaled up, inpainted, scaled back and pasted into the original with a
feathered edge. An optional harmonize pass re-samples a band around the pasted region at low
denoise so colour and lighting blend in.
"""
import io
from dataclasses import dataclass

from PIL import Image, ImageFilter

TARGET_AREA = 1024 * 1024
CONTEXT_FACTOR = 0.6      # padding added around the mask bbox, relative to its size
MIN_CROP_SIDE = 512
AUTO_MAX_MASK_RATIO = 0.25  # bigger masks are inpainted on the whole image


@dataclass
class CropPlan:
    box: tuple[int, int, int, int]   # left, top, right, bottom in original pixels
    size: tuple[int, int]            # size the crop is scaled to for sampling (multiples of 8)


def load_mask(mask_bytes: bytes, size: tuple[int, int]) -> Image.Image:
    """White = repaint (red channel, same convention as the workflow's LoadImageMask)."""
    m = Image.open(io.BytesIO(mask_bytes)).convert("RGB").getchannel("R")
    return m.resize(size).point(lambda v: 255 if v > 127 else 0)


def plan_crop(mask: Image.Image, auto: bool = True) -> CropPlan | None:
    """Returns None when whole-image inpainting is better (no mask, or mask too large in auto mode)."""
    bbox = mask.getbbox()
    if bbox is None:
        return None
    w, h = mask.size
    l, t, r, b = bbox
    if auto and ((r - l) * (b - t)) / (w * h) > AUTO_MAX_MASK_RATIO:
        return None

    bw, bh = r - l, b - t
    pad_x, pad_y = int(bw * CONTEXT_FACTOR), int(bh * CONTEXT_FACTOR)
    l, r = l - pad_x, r + pad_x
    t, b = t - pad_y, b + pad_y

    # guarantee a minimum window so tiny masks still see enough surroundings
    for _ in range(2):
        if r - l < MIN_CROP_SIDE:
            grow = (MIN_CROP_SIDE - (r - l)) // 2 + 1
            l, r = l - grow, r + grow
        if b - t < MIN_CROP_SIDE:
            grow = (MIN_CROP_SIDE - (b - t)) // 2 + 1
            t, b = t - grow, b + grow

    # shift (rather than clip) the window back inside the image where possible
    if l < 0:
        r, l = r - l, 0
    if t < 0:
        b, t = b - t, 0
    if r > w:
        l, r = l - (r - w), w
    if b > h:
        t, b = t - (b - h), h
    l, t, r, b = max(0, l), max(0, t), min(w, r), min(h, b)

    cw, ch = r - l, b - t
    scale = (TARGET_AREA / (cw * ch)) ** 0.5
    tw = max(64, round(cw * scale / 8) * 8)
    th = max(64, round(ch * scale / 8) * 8)
    return CropPlan(box=(l, t, r, b), size=(tw, th))


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def crop_for_sampling(image: Image.Image, mask: Image.Image, plan: CropPlan) -> tuple[bytes, bytes]:
    img = image.crop(plan.box).resize(plan.size, Image.LANCZOS)
    msk = mask.crop(plan.box).resize(plan.size, Image.LANCZOS).point(lambda v: 255 if v > 127 else 0)
    return _png(img.convert("RGB")), _png(msk.convert("RGB"))


def paste_back(
    original: Image.Image, generated_crop: Image.Image, mask: Image.Image, plan: CropPlan, feather: int
) -> Image.Image:
    """Scale the sampled crop back and blend it into `original` through a feathered mask."""
    l, t, r, b = plan.box
    region = generated_crop.convert("RGB").resize((r - l, b - t), Image.LANCZOS)
    alpha = mask.crop(plan.box)
    if feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out = original.convert("RGB").copy()
    base_region = out.crop(plan.box)
    out.paste(Image.composite(region, base_region, alpha), (l, t))
    return out


def harmonize_mask(mask: Image.Image, grow: int = 40, blur: int = 30) -> Image.Image:
    """A wider, softer mask around the pasted region for the low-denoise blending pass."""
    grown = mask.filter(ImageFilter.GaussianBlur(max(1, grow / 2))).point(lambda v: 255 if v > 8 else 0)
    return grown.filter(ImageFilter.GaussianBlur(blur))
