"""Operator commands: `panel create-admin --login NAME` (password from --password or stdin) and `panel migrate`."""

import argparse
import asyncio
import getpass
import sys

from app.db.base import make_engine, make_sessionmaker
from app.db.migrate import upgrade_head
from app.db.models import Admin
from app.security.passwords import hash_password, validate_password


async def create_admin(login: str, password: str, database_url: str) -> None:
    errors = validate_password(password, [login])
    if errors:
        raise SystemExit(f"password rejected: {errors[0]} (min 12 chars, not guessable)")
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            db.add(Admin(login=login, password_hash=hash_password(password)))
            await db.commit()
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    from app.config import get_settings

    parser = argparse.ArgumentParser(prog="panel")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ca = sub.add_parser("create-admin")
    ca.add_argument("--login", required=True)
    ca.add_argument("--password")
    sub.add_parser("migrate")
    args = parser.parse_args(argv)

    url = get_settings().database_url
    if args.cmd == "migrate":
        asyncio.run(upgrade_head(url))
        return
    password = args.password
    if password is None:
        password = getpass.getpass("Password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    asyncio.run(create_admin(args.login, password, url))
    print(f"admin '{args.login}' created")


if __name__ == "__main__":
    main()
