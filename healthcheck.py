from __future__ import annotations

import os
import sys
from urllib.request import urlopen


def main() -> int:
    port = int(os.getenv("PORT", "10000"))
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=4) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
