"""Command-line bootstrap and administrator user registration."""

import argparse
import getpass

from app.security.auth import AuthService
from app.security.user_manager import UserManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Register the initial Matterport Ops administrator")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=("admin",), default="admin")
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    user_id = UserManager(AuthService()).create_user(args.username, password, args.role)
    print(f"Created Matterport Ops administrator {args.username!r} (id={user_id})")


if __name__ == "__main__":
    main()

