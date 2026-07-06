from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.documents import Document

from super_agent.config import settings
from super_agent.knowledge.loaders.base import BaseLoader

logger = logging.getLogger(__name__)

_PADDLEOCR_AVAILABLE: bool | None = None


def _check_paddleocr() -> bool:
    global _PADDLEOCR_AVAILABLE
    if _PADDLEOCR_AVAILABLE is None:
        try:
            import paddleocr  # noqa: F401

            _PADDLEOCR_AVAILABLE = True
        except ImportError:
            _PADDLEOCR_AVAILABLE = False
            logger.warning(
                "paddleocr is not installed. Scanned PDF pages will be skipped. "
                "Install with: uv sync --extra ml"
            )
    return _PADDLEOCR_AVAILABLE


@lru_cache(maxsize=1)
def _get_ocr_engine():
    if not _check_paddleocr():
        return None
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_gpu=settings.ocr.use_gpu, lang=settings.ocr.lang, show_log=False)
    return ocr


class PDFLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        import fitz

        docs = []
        pdf = fitz.open(source)
        skipped_scanned = 0
        for page_num, page in enumerate(pdf, start=1):
            text = page.get_text()
            if self._is_scanned_page(text, page):
                text = self._ocr_page(page)
                if not text:
                    skipped_scanned += 1
                    continue
                docs.append(
                    Document(
                        page_content=text,
                        metadata={"source": source, "page_numbers": [page_num], "ocr_used": True},
                    )
                )
            elif text.strip():
                docs.append(
                    Document(
                        page_content=text,
                        metadata={"source": source, "page_numbers": [page_num], "ocr_used": False},
                    )
                )
        pdf.close()
        if skipped_scanned:
            logger.warning(
                "PDF '%s': %d/%d pages are scanned and no OCR engine available — skipped. "
                "Install with: uv sync --extra ml",
                source, skipped_scanned, skipped_scanned + len(docs),
            )
        return docs

    def _is_scanned_page(self, text: str, page) -> bool:
        if not settings.ocr.enabled:
            return False
        page_area = page.rect.width * page.rect.height
        threshold = page_area * settings.ocr.text_threshold / 1000.0
        return len(text.strip()) < threshold

    def _ocr_page(self, page) -> str:
        engine = _get_ocr_engine()
        if engine is None:
            return ""
        try:
            pixmap = page.get_pixmap(dpi=settings.ocr.page_dpi)
            img_bytes = pixmap.tobytes("png")
            result = engine.ocr(img_bytes, cls=True)
            if not result or not result[0]:
                return ""
            lines = []
            for line in result[0]:
                text = line[1][0]
                lines.append(text)
            return "\n".join(lines)
        except Exception:
            logger.warning("OCR failed for a page, skipping", exc_info=True)
            return ""

    def supported_extensions(self) -> list[str]:
        return [".pdf"]
