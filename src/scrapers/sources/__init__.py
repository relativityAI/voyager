"""Per-source adapters.

Each module registers one :class:`SourceConfig` with the registry. Adding a
new exchange (BSE, SEC, ...) is a matter of adding a module here and a
facade; the transport in ``src/scrapers/`` is reused unchanged (D-09).
"""
