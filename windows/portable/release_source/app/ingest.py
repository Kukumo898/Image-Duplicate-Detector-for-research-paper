"""Bounded, offline image/document decoding for scientific image review.

This module never executes document content or extracts ZIP members onto disk.
Returned PNG files are visual comparison representations, not replacement raw data.
"""
from __future__ import annotations

import io
import math
from pathlib import Path, PurePosixPath
import posixpath
import uuid
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_OUTPUT_PIXELS = 400_000_000
MAX_PDF_PAGE_IMAGE_PIXELS = 160_000_000
MAX_DOCUMENT_PAGES = 200
MAX_ARCHIVE_ENTRIES = 5000
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_RATIO = 500
MAX_XML_BYTES = 16 * 1024 * 1024
PDF_RENDER_DPI = 144
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | {".pdf", ".docx"}


class IngestionError(ValueError):
    """The source cannot be safely decoded into any comparison image."""


def _check_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise IngestionError(
            f"图像尺寸 {width} × {height} 超过安全限制（最多 {MAX_IMAGE_PIXELS:,} 像素）"
        )


def _normalise(image: Image.Image, warnings: list[str], label: str) -> Image.Image:
    _check_dimensions(*image.size)
    image = ImageOps.exif_transpose(image)
    image.load()
    # PIL's direct conversion clips 16-bit microscopy images; explicitly produce
    # a linear visual representation instead and disclose the transformation.
    if image.mode in {"I", "F", "I;16", "I;16B", "I;16L", "I;16N"}:
        values = np.asarray(image, dtype=np.float64)
        finite = np.isfinite(values)
        if not finite.any():
            raise IngestionError("高位深图像不含有限像素值")
        low, high = float(values[finite].min()), float(values[finite].max())
        scaled = np.zeros(values.shape, dtype=np.uint8)
        if high > low:
            scaled[finite] = np.rint((values[finite] - low) * 255 / (high - low)).astype(np.uint8)
        elif low > 0:
            scaled[finite] = 255
        warnings.append(f"{label}：高位深像素已线性映射至 8 位查重视图；不用于定量分析。")
        image = Image.fromarray(scaled)
    if image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


class _Collector:
    def __init__(self, output_dir: Path, source: str, max_images: int):
        self.output_dir = output_dir
        self.source = source
        self.max_images = max_images
        self.images: list[dict] = []
        self.warnings: list[str] = []
        self.limit_reported = False
        self.output_pixels = 0

    def full(self) -> bool:
        if len(self.images) < self.max_images:
            return False
        if not self.limit_reported:
            self.warnings.append(f"达到每文件 {self.max_images} 张图像上限，后续内容未处理；本文件未全扫。")
            self.limit_reported = True
        return True

    def add(self, image: Image.Image, name: str, page: int | None,
            representation: str, source_index: int | None = None) -> None:
        if self.output_pixels + image.width * image.height > MAX_OUTPUT_PIXELS:
            raise IngestionError(f"文件累计输出超过 {MAX_OUTPUT_PIXELS:,} 像素上限")
        image = _normalise(image, self.warnings, name)
        output = self.output_dir / f"{uuid.uuid4().hex}.png"
        try:
            image.save(output, format="PNG")
        except Exception:
            output.unlink(missing_ok=True)
            raise
        record = {"path": str(output.resolve()), "name": name, "source": self.source,
                  "page": page, "width": image.width, "height": image.height,
                  "representation": representation}
        if source_index is not None:
            record["source_index"] = source_index
        self.images.append(record)
        self.output_pixels += image.width * image.height

    def skipped(self, label: str, error: Exception | str) -> None:
        self.warnings.append(f"{label} 未能处理：{error}；本文件未全扫。")


def _raster(source: Path, collector: _Collector) -> None:
    with Image.open(source) as image:
        frame_count = getattr(image, "n_frames", 1)
        count = min(frame_count, MAX_DOCUMENT_PAGES)
        if frame_count > count:
            collector.warnings.append(f"图像含 {frame_count} 帧，超过 {count} 帧上限；本文件未全扫。")
        for index in range(count):
            if collector.full():
                break
            label = collector.source if frame_count == 1 else f"{collector.source} · 第 {index + 1} 帧"
            try:
                image.seek(index)
                collector.add(image, label, index + 1 if frame_count > 1 else None,
                              "image", index + 1)
            except (OSError, ValueError, EOFError, Image.DecompressionBombError) as error:
                collector.skipped(label, error)


def _pdf(source: Path, collector: _Collector) -> None:
    import pymupdf as fitz

    collector.warnings.append(
        "PDF 同时保留整页视图和内嵌图。整页含文字、图注与矢量内容，尚未进行语义分 panel；"
        "不同表示之间的相似结果可能来自同一内容，请结合页码复核。"
    )
    with fitz.open(source) as document:
        if document.needs_pass:
            raise IngestionError("PDF 受密码保护，请先提供可读取的副本")
        pages = min(len(document), MAX_DOCUMENT_PAGES)
        if len(document) > pages:
            collector.warnings.append(f"PDF 有 {len(document)} 页，超过 {pages} 页上限；本文件未全扫。")
        for index in range(pages):
            if collector.full():
                break
            label = f"{collector.source} · 第 {index + 1} 页"
            try:
                page = document.load_page(index)
            except Exception as error:
                collector.skipped(label, error)
                continue
            try:
                # Query dimensions without hashes first: image hashing/rendering
                # can otherwise decompress a huge image before our size guard.
                resources = page.get_images(full=True)
                geometry = page.get_image_info()
                for item in geometry:
                    _check_dimensions(item["width"], item["height"])
                if sum(item["width"] * item["height"] for item in geometry) > MAX_PDF_PAGE_IMAGE_PIXELS:
                    raise IngestionError("PDF 单页内嵌图像累计像素超过安全限制")
            except Exception as error:
                collector.skipped(label + "图像尺寸预检", error)
                continue
            try:
                scale = PDF_RENDER_DPI / 72
                _check_dimensions(math.ceil(page.rect.width * scale), math.ceil(page.rect.height * scale))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                collector.add(image, label + " · 整页", index + 1, "page", index + 1)
            except Exception as error:
                collector.skipped(label + "整页渲染", error)
            try:
                mask_by_xref = {resource[0]: resource[1] for resource in resources}
                occurrences = page.get_image_info(xrefs=True)
            except Exception as error:
                collector.skipped(label + "内嵌图读取", error)
                continue
            for embedded_index, occurrence in enumerate(occurrences, 1):
                xref = occurrence["xref"]
                width, height = occurrence["width"], occurrence["height"]
                if collector.full():
                    break
                image_label = f"{label} · 内嵌图 {embedded_index}"
                try:
                    _check_dimensions(width, height)
                    if not xref:
                        clip = fitz.Rect(occurrence["bbox"]) & page.rect
                        if clip.is_empty or clip.is_infinite:
                            raise IngestionError("内联图像位置无效")
                        _check_dimensions(math.ceil(clip.width * scale), math.ceil(clip.height * scale))
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip,
                                                alpha=False, colorspace=fitz.csRGB)
                        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                        collector.add(image, image_label + " · 内联区域渲染", index + 1,
                                      "embedded", embedded_index)
                        collector.images[-1]["bbox"] = list(occurrence["bbox"])
                        collector.warnings.append(f"{image_label} 无独立图像资源，使用页面区域渲染，可能包含叠加文字。")
                        continue
                    pixmap = fitz.Pixmap(document, xref)
                    if pixmap.colorspace is None:
                        raise IngestionError("无可用色彩空间的图像蒙版")
                    if pixmap.colorspace.n not in {1, 3}:
                        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                    mask = mask_by_xref.get(xref, 0)
                    if mask and not pixmap.alpha:
                        pixmap = fitz.Pixmap(pixmap, fitz.Pixmap(document, mask))
                    # PNG decoding handles gray/RGB and soft masks consistently.
                    with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                        collector.add(image, image_label, index + 1, "embedded", embedded_index)
                        collector.images[-1]["bbox"] = list(occurrence["bbox"])
                except Exception as error:
                    collector.skipped(image_label, error)


def _read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise IngestionError("DOCX XML 体积超过安全限制")
    data = archive.read(info)
    declarations = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise IngestionError("DOCX XML 包含不允许的 DTD/实体声明")
    return ET.fromstring(data)


def _document_nodes(node: ET.Element):
    """Do not count AlternateContent Choice and its fallback as two images."""
    yield node
    if node.tag.rsplit("}", 1)[-1] == "AlternateContent":
        choices = [child for child in node if child.tag.rsplit("}", 1)[-1] == "Choice"]
        fallback = [child for child in node if child.tag.rsplit("}", 1)[-1] == "Fallback"]
        children = (choices or fallback)[:1]
    else:
        children = node
    for child in children:
        yield from _document_nodes(child)


def _docx(source: Path, collector: _Collector) -> None:
    collector.warnings.append(
        "DOCX 按正文中的每次图片引用提取可解码的嵌入位图，无可靠页码；"
        "矢量图、Office 图表/形状、外链图像和布局渲染不在此次提取范围，文档未全扫。"
    )
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise IngestionError("DOCX ZIP 条目数超过安全限制")
        expanded = 0
        names: set[str] = set()
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or "\x00" in name or "\\" in entry.filename:
                raise IngestionError("DOCX 包含不安全的 ZIP 路径")
            if name in names:
                raise IngestionError("DOCX 包含重复 ZIP 条目")
            names.add(name)
            expanded += entry.file_size
            if expanded > MAX_ARCHIVE_EXPANDED_BYTES or entry.file_size > MAX_INPUT_BYTES:
                raise IngestionError("DOCX 解压后体积超过安全限制")
            if entry.file_size > 1024 * 1024 and entry.file_size / max(entry.compress_size, 1) > MAX_ARCHIVE_RATIO:
                raise IngestionError("DOCX 压缩比异常，拒绝解码")
            if entry.flag_bits & 1:
                raise IngestionError("不支持加密的 DOCX ZIP 条目")
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise IngestionError("文件不是有效的 DOCX 包")
        media = {entry.filename.replace("\\", "/"): entry for entry in entries
                 if entry.filename.replace("\\", "/").startswith("word/media/") and not entry.is_dir()}
        relationships: dict[str, str | None] = {}
        rels_name = "word/_rels/document.xml.rels"
        if rels_name in names:
            for relation in _read_xml(archive, rels_name):
                identifier = relation.get("Id")
                if not identifier:
                    continue
                if identifier in relationships:
                    raise IngestionError("DOCX 包含重复关系 ID")
                target = unquote(relation.get("Target", "")).replace("\\", "/")
                if relation.get("TargetMode", "").lower() == "external":
                    relationships[identifier] = None
                else:
                    resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("word", target))
                    relationships[identifier] = resolved if resolved.startswith("word/media/") else None
        document = _read_xml(archive, "word/document.xml")
        r_namespaces = ("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}",
                        "{http://purl.oclc.org/ooxml/officeDocument/relationships}")
        requested: list[tuple[str, str]] = []
        for node in _document_nodes(document):
            # DrawingML a:blip and legacy VML v:imagedata both occur in DOCX.
            if node.tag.rsplit("}", 1)[-1] not in {"blip", "imagedata"}:
                continue
            identifier = next((node.get(ns + attr) for ns in r_namespaces for attr in ("embed", "id")
                               if node.get(ns + attr)), None)
            if identifier:
                target = relationships.get(identifier)
                if target in media:
                    requested.append((target, "正文引用"))
                else:
                    collector.skipped(f"DOCX 图片引用 {identifier}", "未找到内部位图关系，可能是外链或非位图内容")
            elif any(node.get(ns + "link") for ns in r_namespaces):
                collector.skipped("DOCX 外链图像", "离线模式不访问外部链接")
        referenced = {name for name, _ in requested}
        unreferenced = [name for name in media if name not in referenced]
        if unreferenced:
            collector.warnings.append(
                f"另有 {len(unreferenced)} 个媒体资源未在正文中引用，按资源各提取一次；"
                "可能来自页眉/页脚或未使用资源，不能据此确定实际出现次数，文档未全扫。"
            )
            requested.extend((name, "非正文媒体资源") for name in unreferenced)
        for index, (media_name, context) in enumerate(requested, 1):
            if collector.full():
                break
            entry = media[media_name]
            label = f"{collector.source} · {context} {index} ({PurePosixPath(entry.filename).name})"
            try:
                with archive.open(entry) as stream:
                    data = stream.read(MAX_INPUT_BYTES + 1)
                if len(data) > MAX_INPUT_BYTES:
                    raise IngestionError("内嵌图体积超过安全限制")
                with Image.open(io.BytesIO(data)) as image:
                    frames = getattr(image, "n_frames", 1)
                    if frames > 1:
                        collector.warnings.append(f"{label} 含 {frames} 帧，仅提取首帧；本文件未全扫。")
                    collector.add(image, label, None, "embedded", index)
            except (OSError, ValueError, EOFError, zipfile.BadZipFile, Image.DecompressionBombError) as error:
                collector.skipped(label, error)


def ingest_file(source_path: Path, display_name: str, output_dir: Path,
                max_images: int = 300) -> tuple[list[dict], list[str]]:
    """Decode one source locally, returning normalized image records and warnings.

    Partial results are always accompanied by a warning containing ``未全扫``.
    Unsupported, unsafe, unreadable, or zero-image sources raise IngestionError.
    Original input files are never modified.
    """
    source_path, output_dir = Path(source_path), Path(output_dir)
    if not isinstance(max_images, int) or isinstance(max_images, bool) or not 1 <= max_images <= 1000:
        raise IngestionError("max_images 必须为 1 至 1000 的整数")
    if not source_path.is_file():
        raise IngestionError("输入文件不存在或不是普通文件")
    size = source_path.stat().st_size
    if size <= 0 or size > MAX_INPUT_BYTES:
        raise IngestionError(f"输入文件为空或超过 {MAX_INPUT_BYTES // 1024 // 1024} MiB 上限")
    suffix = Path(display_name).suffix.lower() or source_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        if suffix == ".doc":
            raise IngestionError("不支持旧版 .doc，请另存为 .docx 或 PDF")
        raise IngestionError(f"不支持的文件格式：{suffix or '无扩展名'}")
    output_dir.mkdir(parents=True, exist_ok=True)
    collector = _Collector(output_dir, display_name, max_images)
    try:
        if suffix in IMAGE_SUFFIXES:
            _raster(source_path, collector)
        elif suffix == ".pdf":
            _pdf(source_path, collector)
        else:
            _docx(source_path, collector)
    except IngestionError:
        raise
    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile, UnidentifiedImageError, ET.ParseError,
            Image.DecompressionBombError) as error:
        if collector.images:
            collector.skipped(display_name, error)
        else:
            raise IngestionError(f"无法读取 {display_name}：{error}") from error
    if not collector.images:
        details = "；".join(collector.warnings[-3:])
        raise IngestionError(f"{display_name} 未提取到可用图像。{details}")
    return collector.images, collector.warnings
