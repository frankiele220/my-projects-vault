"""macOS only: decode pictures, read their text and find faces (Vision), draw boxes and save PNG (Quartz). Nothing leaves this Mac."""
from __future__ import annotations

import os

from . import config

PDF_PAGES = 12
PDF_TEXT_MAX = 400   # a PDF page with less text than this is mostly a picture (slide, screenshot, diagram)


def from_bytes(data: bytes):
    import Quartz
    from Foundation import NSData
    src = Quartz.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(data, len(data)), None)
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None) if src else None


def size(img) -> tuple:
    import Quartz
    return Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)


def read(img) -> tuple:
    """[(text, (x, y, w, h))] of text lines and [(x, y, w, h)] of faces, boxes in 0..1 with the origin at the bottom left."""
    import Vision
    lines, faces = [], []
    txt = Vision.VNRecognizeTextRequest.alloc().init()
    txt.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    txt.setUsesLanguageCorrection_(False)
    face = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, _err = handler.performRequests_error_([txt, face], None)
    if not ok:
        raise RuntimeError("Vision could not read the picture")
    for o in txt.results() or []:
        c = o.topCandidates_(1)
        if c:
            b = o.boundingBox()
            lines.append((str(c[0].string()), (b.origin.x, b.origin.y, b.size.width, b.size.height)))
    for o in face.results() or []:
        b = o.boundingBox()
        faces.append((b.origin.x, b.origin.y, b.size.width, b.size.height))
    return lines, faces


def draw(img, boxes: list):
    """A copy at most OUT_W wide (never enlarged) with black boxes."""
    import Quartz
    w, h = size(img)
    s = min(1.0, config.OUT_W / w)
    W, H = max(1, int(w * s)), max(1, int(h * s))
    ctx = Quartz.CGBitmapContextCreate(None, W, H, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(), Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, W, H))
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, W, H), img)
    Quartz.CGContextSetRGBFillColor(ctx, 0, 0, 0, 1)
    for x, y, bw, bh in boxes:
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(x * W, y * H, bw * W, bh * H))
    return Quartz.CGBitmapContextCreateImage(ctx)


def save(img, path: str) -> None:
    import Quartz
    from Foundation import NSURL
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.png"
    dest = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(tmp), "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, img, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError("could not write the picture")
    os.replace(tmp, path)


def pdf_text(path: str, pages: int = 6) -> str:
    from Foundation import NSURL
    from Quartz import PDFDocument
    doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
    if doc is None:
        return ""
    return "\n".join((doc.pageAtIndex_(i).string() or "") for i in range(min(doc.pageCount(), pages)))


def pdf_pages(path: str):
    """(page number, CGImage) of PDF pages that are mostly a picture, rendered OUT_W wide."""
    import Quartz
    from Foundation import NSURL
    from Quartz import PDFDocument
    doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
    cg = Quartz.CGPDFDocumentCreateWithURL(NSURL.fileURLWithPath_(path))
    if doc is None or cg is None:
        return
    for i in range(min(doc.pageCount(), PDF_PAGES)):
        if len(doc.pageAtIndex_(i).string() or "") >= PDF_TEXT_MAX:
            continue
        page = Quartz.CGPDFDocumentGetPage(cg, i + 1)
        box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        if box.size.width <= 0 or box.size.height <= 0:
            continue
        s = config.OUT_W / box.size.width
        W, H = int(box.size.width * s), int(box.size.height * s)
        ctx = Quartz.CGBitmapContextCreate(None, W, H, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(), Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, W, H))
        Quartz.CGContextScaleCTM(ctx, s, s)
        Quartz.CGContextDrawPDFPage(ctx, page)
        yield i + 1, Quartz.CGBitmapContextCreateImage(ctx)
