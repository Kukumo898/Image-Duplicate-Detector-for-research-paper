"""Document-mode end-to-end tests use actual PDF/DOCX decoding and jobs."""
import io
import time
import zipfile

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pymupdf
import pytest

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path / "data")
    with TestClient(main.app) as test_client:
        try:
            yield test_client
        finally:
            # Drain the shared single-worker executor before restoring DATA.
            main.POOL.submit(lambda: None).result(timeout=30)


def image_content():
    rng = np.random.default_rng(20261021)
    image = Image.fromarray(rng.integers(20, 230, (120, 160, 3), dtype=np.uint8))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def submit_document(client, name, content, media_type):
    response = client.post("/api/jobs", files=[("files", (name, content, media_type))],
                           data={"mode": "documents", "add_to_library": "false"})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["job"]["status"] not in {"queued", "running", "cancelling"}:
            return detail
        time.sleep(0.05)
    pytest.fail("Document job did not finish within 20 seconds")


def test_pdf_job_detects_reuse_and_omits_structural_pairs(client):
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    xref = page.insert_image(pymupdf.Rect(10, 20, 138, 116), stream=image_content())
    page.insert_image(pymupdf.Rect(160, 20, 288, 116), xref=xref)
    content = document.tobytes()
    document.close()

    detail = submit_document(client, "reused-panels.pdf", content, "application/pdf")
    assert detail["job"]["status"] == "completed", detail["job"]
    assert len(detail["images"]) == 3
    assert all(image["page"] == 1 for image in detail["images"])
    assert all(image["source"] == "reused-panels.pdf" for image in detail["images"])
    assert detail["job"]["total_pairs"] == 1
    assert detail["job"]["processed_pairs"] == 1
    assert len(detail["findings"]) == 1
    finding = detail["findings"][0]
    assert finding["kind"] == "exact_pixels"
    assert finding["image_a"]["representation"] == "embedded"
    assert finding["image_b"]["representation"] == "embedded"
    assert finding["image_a"]["id"] != finding["image_b"]["id"]


def test_docx_job_detects_reused_reference_without_inventing_pages(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", '''
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <w:body><w:p><a:blip r:embed="rId1"/></w:p><w:p><a:blip r:embed="rId1"/></w:p></w:body>
            </w:document>''')
        archive.writestr("word/_rels/document.xml.rels", '''
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="media/figure.png"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>
            </Relationships>''')
        archive.writestr("word/media/figure.png", image_content())
    detail = submit_document(client, "reused-panels.docx", buffer.getvalue(),
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert detail["job"]["status"] == "completed", detail["job"]
    assert len(detail["images"]) == 2
    assert all(image["page"] is None for image in detail["images"])
    assert all(image["source"] == "reused-panels.docx" for image in detail["images"])
    assert detail["job"]["processed_pairs"] == 1
    assert len(detail["findings"]) == 1
    assert detail["findings"][0]["kind"] == "exact_pixels"
    assert any("无可靠页码" in warning for warning in detail["job"]["warnings"])
