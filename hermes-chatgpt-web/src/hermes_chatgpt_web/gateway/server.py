"""
Internal Gateway module.

In Hermes 1.0.0+, the internal gateway runs as an integrated module within the
single FastAPI process on ADAPTER_PORT (18111).
"""

from ..main import main

if __name__ == "__main__":
    main()
