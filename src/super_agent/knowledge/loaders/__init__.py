from super_agent.knowledge.loaders.base import BaseLoader
from super_agent.knowledge.loaders.pdf import PDFLoader
from super_agent.knowledge.loaders.word import WordLoader
from super_agent.knowledge.loaders.markdown import MarkdownLoader
from super_agent.knowledge.loaders.html import HTMLLoader
from super_agent.knowledge.loaders.structured import JSONLoader, YAMLLoader, CSVLoader
from super_agent.knowledge.loaders.excel import ExcelLoader
from super_agent.knowledge.loaders.ppt import PPTLoader

_REGISTRY: dict[str, type[BaseLoader]] = {}


def _register(loader_cls: type[BaseLoader]) -> None:
    for ext in loader_cls().supported_extensions():
        _REGISTRY[ext.lower()] = loader_cls


_register(PDFLoader)
_register(WordLoader)
_register(MarkdownLoader)
_register(HTMLLoader)
_register(JSONLoader)
_register(YAMLLoader)
_register(CSVLoader)
_register(ExcelLoader)
_register(PPTLoader)


def get_loader(extension: str) -> BaseLoader:
    ext = extension.lower()
    if ext not in _REGISTRY:
        raise ValueError(f"Unsupported file extension: {ext}")
    return _REGISTRY[ext]()


def supported_extensions() -> list[str]:
    return list(_REGISTRY.keys())
