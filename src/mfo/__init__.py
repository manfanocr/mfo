"""mfo — Manhua Fanyi OCR / Translation.

A local-first manga/manhua OCR and context-aware translation pipeline.

The package is organized into layers (see docs/ARCHITECTURE.md):

- ``core``      data models, project state, pipeline orchestration
- ``vision``    region detection and OCR adapters
- ``language``  translation adapters, glossary, context, AI assist
- ``render``    masking, font fitting, text placement, compositing
- ``storage``   project files, SQLite, caches, exports
- ``cli``       headless / scriptable entry point
- ``ui``        local review editor
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
