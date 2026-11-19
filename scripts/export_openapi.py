"""Write the API's OpenAPI document to openapi.json (or check that it is up to date).

python scripts/export_openapi.py            # write openapi.json
python scripts/export_openapi.py --check    # exit 1 if the committed file is stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from helpdesk.api.app import create_app
from helpdesk.config import Settings

TARGET = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:", log_json=False))
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != content:
            print("openapi.json is out of date. Run `make openapi` and commit the result.")
            return 1
        print("openapi.json is up to date.")
        return 0
    TARGET.write_text(content)
    count = sum(len(item) for item in json.loads(content)["paths"].values())
    print(f"Wrote {TARGET} ({count} operations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
