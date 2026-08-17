"""Launch the Voyager CLI against a database profile.

Lets you switch between your local PostgreSQL and Supabase for writes:

    python scripts/cli.py --profile local  pull VBL
    python scripts/cli.py --profile atlas  pull VBL

The profile name maps to profiles/<profile>.env (see the .example files).
Set VOYAGER_PROFILE to change the default.
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", default=os.getenv("VOYAGER_PROFILE", "local"))
    parser.add_argument(
        "--env-file",
        help="Path to an env file to load instead of profiles/<profile>.env",
    )
    args, remaining = parser.parse_known_args()

    env_file = (
        Path(args.env_file) if args.env_file else PROFILES_DIR / f"{args.profile}.env"
    )
    if not env_file.exists():
        sys.exit(
            f"Profile env file not found: {env_file}\nCreate it from {env_file}.example"
        )

    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from cli import app

    app(args=remaining)


if __name__ == "__main__":
    main()
