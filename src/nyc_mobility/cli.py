import argparse
import sys

from nyc_mobility import __version__, db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nyc-mobility")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    db_parser = commands.add_parser("db", help="Operações no banco nyc_mobility")
    db_actions = db_parser.add_subparsers(dest="action", required=True)
    db_actions.add_parser("upgrade", help="Aplica migrações Alembic")
    db_actions.add_parser("check", help="Verifica conexão e schemas")
    args = parser.parse_args(argv)

    if args.action == "upgrade":
        db.upgrade()
    missing = db.missing_schemas()
    if missing:
        print(f"Schemas ausentes: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"OK: conexão estabelecida; schemas presentes: {', '.join(db.SCHEMAS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
