import io
from pathlib import Path
import zipfile

import pymupdf as fitz
import numpy as np
from PIL import Image
import pytest

from app import ingest


def test_transparency_is_composited_onto_white(tmp_path):
    source = tmp_path / "alpha.png"
    image = Image.new("RGBA", (10, 12), (230, 20, 10, 0))
    image.putpixel((1, 1), (230, 20, 10, 255))
    image.save(source)
    images, warnings = ingest.ingest_file(source, source.name, tmp_path / "out")
    output = Image.open(images[0]["path"])
    assert output.mode == "RGB"
    assert output.getpixel((0, 0)) == (255, 255, 255)
    assert output.getpixel((1, 1)) == (230, 20, 10)
    assert images[0]["representation"] == "image"
    assert images[0]["page"] is None
    assert not warnings


def test_exif_orientation_is_applied(tmp_path):
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (24, 12), "navy")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    images, _ = ingest.ingest_file(source, source.name, tmp_path / "out")
    assert (images[0]["width"], images[0]["height"]) == (12, 24)
    assert Image.open(images[0]["path"]).getexif().get(274) is None


def test_tiff_frames_and_explicit_limit(tmp_path):
    source = tmp_path / "stack.tiff"
    frames = [Image.new("RGB", (12, 14), color) for color in ("red", "green", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:])
    images, warnings = ingest.ingest_file(source, source.name, tmp_path / "out", max_images=2)
    assert [record["page"] for record in images] == [1, 2]
    assert any("未全扫" in warning and "2" in warning for warning in warnings)


def test_high_bit_depth_is_scaled_without_saturation(tmp_path):
    source = tmp_path / "raw.tiff"
    Image.fromarray(np.array([[1000, 2000], [3000, 4000]], dtype=np.uint16)).save(source)
    images, warnings = ingest.ingest_file(source, source.name, tmp_path / "out")
    values = np.asarray(Image.open(images[0]["path"]))[:, :, 0]
    assert values.tolist() == [[0, 85], [170, 255]]
    assert any("高位深" in warning for warning in warnings)


def test_pdf_preserves_page_and_embedded_image(tmp_path):
    source = tmp_path / "figure.pdf"
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_image(fitz.Rect(20, 20, 100, 80), stream=buffer.getvalue())
    document.new_page(width=200, height=200).draw_circle(fitz.Point(80, 80), 30)
    document.save(source)
    document.close()
    images, warnings = ingest.ingest_file(source, source.name, tmp_path / "out")
    assert {(row["page"], row["representation"]) for row in images} == {
        (1, "page"), (1, "embedded"), (2, "page")}
    assert any("语义分 panel" in warning for warning in warnings)


def _make_docx(path: Path, extra: dict[str, bytes], duplicate=False, document_xml=None):
    media = [name for name in extra if name.startswith("word/media/")]
    namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    embeds = [f'<a:blip r:embed="rId{i}"/>' for i, _ in enumerate(media, 1)]
    if duplicate and embeds:
        embeds.append(embeds[0])
    document = document_xml or (f'<document xmlns:a="urn:drawing" xmlns:r="{namespace}">' + "".join(embeds) + "</document>")
    relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    for index, name in enumerate(media, 1):
        relationships += f'<Relationship Id="rId{index}" Target="{name[len("word/"):]}" Type="image"/>'
    relationships += "</Relationships>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        for name, data in extra.items():
            archive.writestr(name, data)


def test_docx_extracts_images_without_claiming_page_numbers(tmp_path):
    source = tmp_path / "article.docx"
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "teal").save(buffer, format="PNG")
    _make_docx(source, {"word/media/image1.png": buffer.getvalue(),
                        "word/media/vector.emf": b"not a bitmap"})
    images, warnings = ingest.ingest_file(source, source.name, tmp_path / "out")
    assert len(images) == 1
    assert images[0]["page"] is None
    assert images[0]["representation"] == "embedded"
    assert any("无可靠页码" in warning for warning in warnings)
    assert any("vector.emf" in warning and "未全扫" in warning for warning in warnings)


def test_docx_path_traversal_is_rejected(tmp_path):
    source = tmp_path / "unsafe.docx"
    _make_docx(source, {"word/media/../../escape.png": b"bad"})
    with pytest.raises(ingest.IngestionError, match="不安全"):
        ingest.ingest_file(source, source.name, tmp_path / "out")
    assert not (tmp_path / "escape.png").exists()


def test_docx_repeated_reference_preserves_both_occurrences(tmp_path):
    source = tmp_path / "reused.docx"
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "teal").save(buffer, format="PNG")
    _make_docx(source, {"word/media/image1.png": buffer.getvalue()}, duplicate=True)
    images, _ = ingest.ingest_file(source, source.name, tmp_path / "out")
    assert len(images) == 2
    assert [image["source_index"] for image in images] == [1, 2]
    assert images[0]["path"] != images[1]["path"]
    assert Path(images[0]["path"]).read_bytes() == Path(images[1]["path"]).read_bytes()


def test_docx_choice_and_fallback_are_not_false_duplicate_occurrences(tmp_path):
    source = tmp_path / "fallback.docx"
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "teal").save(buffer, format="PNG")
    document = '''<document xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">
        <mc:AlternateContent><mc:Choice><blip r:embed="rId1"/></mc:Choice>
        <mc:Fallback><imagedata r:id="rId1"/></mc:Fallback></mc:AlternateContent></document>'''
    _make_docx(source, {"word/media/image1.png": buffer.getvalue()}, document_xml=document)
    images, _ = ingest.ingest_file(source, source.name, tmp_path / "out")
    assert len(images) == 1


def test_pdf_reused_resource_preserves_both_occurrences(tmp_path):
    source = tmp_path / "reused.pdf"
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    xref = page.insert_image(fitz.Rect(10, 10, 90, 70), stream=buffer.getvalue())
    page.insert_image(fitz.Rect(100, 100, 180, 160), xref=xref)
    document.save(source)
    document.close()
    images, _ = ingest.ingest_file(source, source.name, tmp_path / "out")
    embedded = [row for row in images if row["representation"] == "embedded"]
    assert len(embedded) == 2
    assert embedded[0]["bbox"] != embedded[1]["bbox"]


def test_docx_expansion_is_bounded(tmp_path, monkeypatch):
    source = tmp_path / "large.docx"
    _make_docx(source, {"word/media/image.png": b"a" * 200})
    monkeypatch.setattr(ingest, "MAX_ARCHIVE_EXPANDED_BYTES", 100)
    with pytest.raises(ingest.IngestionError, match="体积"):
        ingest.ingest_file(source, source.name, tmp_path / "out")


@pytest.mark.parametrize("suffix,content", [(".png", b"corrupt"), (".pdf", b"invalid pdf"),
                                            (".docx", b"not a zip"), (".doc", b"legacy")])
def test_bad_inputs_fail_explicitly(tmp_path, suffix, content):
    source = tmp_path / ("broken" + suffix)
    source.write_bytes(content)
    with pytest.raises(ingest.IngestionError):
        ingest.ingest_file(source, source.name, tmp_path / "out")


def test_image_pixel_limit_is_enforced_before_decoding(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (20, 20), "white").save(source)
    monkeypatch.setattr(ingest, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ingest.IngestionError, match="像素"):
        ingest.ingest_file(source, source.name, tmp_path / "out")


def test_output_names_are_unique_and_input_unchanged(tmp_path):
    source = tmp_path / "same.png"
    Image.new("RGB", (10, 10), "black").save(source)
    before = source.read_bytes()
    first, _ = ingest.ingest_file(source, "same.png", tmp_path / "out")
    second, _ = ingest.ingest_file(source, "same.png", tmp_path / "out")
    assert first[0]["path"] != second[0]["path"]
    assert source.read_bytes() == before
