"""Audited bibliography exporters for canonical Litminer records."""

__all__ = ["export_bibliography"]


def __getattr__(name: str):
    if name == "export_bibliography":
        from .exporter import export_bibliography
        return export_bibliography
    raise AttributeError(name)
