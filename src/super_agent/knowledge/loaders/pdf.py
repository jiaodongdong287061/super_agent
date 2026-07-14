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

        pdf = fitz.open(source)
        page_docs = self._extract_pages(pdf, source)
        pdf.close()
        return self._merge_cross_page_tables(page_docs)

    def _extract_pages(self, pdf, source: str) -> list[tuple[str, int, bool]]:
        """Extract each page as (text, page_number, table_continues).

        Returns a list of tuples for downstream cross-page table merging.
        table_continues=True means the page ends with a table boundary
        that likely spans to the next page.
        """
        pages: list[tuple[str, int, bool]] = []
        skipped_scanned = 0
        total_scanned = 0
        for page_num, page in enumerate(pdf, start=1):
            text = page.get_text()
            if self._is_scanned_page(text, page):
                text = self._ocr_page(page)
                if not text:
                    skipped_scanned += 1
                    continue
                total_scanned += 1
                table_continues = self._has_cross_page_table(page)
                pages.append((text, page_num, table_continues))
            elif text.strip():
                table_continues = self._has_cross_page_table(page)
                pages.append((text, page_num, table_continues))

        if skipped_scanned:
            logger.warning(
                "PDF '%s': %d/%d pages are scanned and no OCR engine available — skipped. "
                "Install with: uv sync --extra ml",
                source, skipped_scanned, skipped_scanned + len(pages) + total_scanned,
            )
        return pages

    def _merge_cross_page_tables(
        self, pages: list[tuple[str, int, bool]]
    ) -> list[Document]:
        """Merge consecutive pages where a table spans page boundaries."""
        if not pages:
            return []

        docs: list[Document] = []
        accumulated_texts: list[str] = []
        accumulated_page_nums: list[int] = []

        for text, page_num, table_continues in pages:
            accumulated_texts.append(text)
            accumulated_page_nums.append(page_num)
            if not table_continues:
                docs.append(
                    Document(
                        page_content="\n\n".join(accumulated_texts),
                        metadata={
                            "source": "pdf",
                            "page_numbers": list(accumulated_page_nums),
                        },
                    )
                )
                accumulated_texts = []
                accumulated_page_nums = []

        # Flush remaining accumulated pages
        if accumulated_texts:
            docs.append(
                Document(
                    page_content="\n\n".join(accumulated_texts),
                    metadata={
                        "source": "pdf",
                        "page_numbers": list(accumulated_page_nums),
                    },
                )
            )
        return docs

    @staticmethod
    def _has_cross_page_table(page) -> bool:
        """Check if the page has a table at its bottom edge, indicating it spans to next page."""
        try:
            tables = page.find_tables()
            page_height = page.rect.height
            # If a table's bottom is within 3% of the page height, it likely continues
            threshold = page_height * 0.03
            for table in tables:
                if page_height - table.bbox.y1 < threshold:
                    return True
            return False
        except Exception:
            return False

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
