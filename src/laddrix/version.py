"""Laddrix's version (V1.6, #289): one place, shown by /api/health and on the screens.

Semantic versions: MAJOR.MINOR.PATCH, with "-rc.N" for a release candidate. pyproject.toml
(as PEP 440: 1.0.0rc1) and ui/package.json carry the same number; a test keeps them in step.
`BUILD` is the commit the image was built from (Dockerfile: --build-arg LADDRIX_BUILD=…),
"dev" when run from a checkout.
"""

import os

VERSION = "1.0.0-rc.1"
BUILD = os.environ.get("LADDRIX_BUILD", "dev")
