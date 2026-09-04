"""Top-level verification CLI module."""

import sys
from pathlib import Path
from advisor.audit.certificate import verify_certificate

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cert.json")
    success = verify_certificate(target)
    sys.exit(0 if success else 1)
