import argparse
import json
import sys

from nyc_mobility import __version__, db
from nyc_mobility.build import build
from nyc_mobility.common import ContractError
from nyc_mobility.ingest import ADAPTERS, ingest
from nyc_mobility.sync import synchronize

# Código de saída para arquivo fora do contrato (2 já é usado pelo argparse para uso inválido).
EXIT_CONTRACT_ERROR = 3
EXIT_BUILD_ERROR = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nyc-mobility")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    db_parser = commands.add_parser("db", help="Operações no banco nyc_mobility")
    db_actions = db_parser.add_subparsers(dest="action", required=True)
    db_actions.add_parser("upgrade", help="Aplica migrações Alembic")
    db_actions.add_parser("check", help="Verifica conexão e schemas")

    build_parser = commands.add_parser(
        "build", help="Executa dbt build com rastreamento em metadata"
    )
    build_parser.add_argument(
        "--period", help="Competência solicitada (o dbt verifica o projeto inteiro)"
    )

    sync_parser = commands.add_parser(
        "sync", help="Descobre publicações e processa meses pendentes"
    )
    sync_parser.add_argument("--start-period", required=True)
    sync_parser.add_argument("--end-period", help="Limite AAAA-MM; padrão: último mês completo")
    sync_parser.add_argument("--include-hvfhv", action="store_true")
    sync_parser.add_argument("--max-periods", type=int, default=1)
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="Só descobre; sem ingestão/build"
    )
    build_parser.add_argument(
        "--full-refresh", action="store_true", help="Reconstrói todos os modelos"
    )

    ingest_parser = commands.add_parser("ingest", help="Ingere um dataset")
    ingest_parser.add_argument("--dataset", required=True, choices=sorted(ADAPTERS))
    ingest_parser.add_argument(
        "--period", help="Competência AAAA-MM (omitir para referências, ex.: tlc.taxi_zones)"
    )
    ingest_parser.add_argument(
        "--force", action="store_true", help="Recarrega mesmo se o hash já estiver publicado"
    )
    args = parser.parse_args(argv)

    if args.command == "sync":
        try:
            result = synchronize(
                args.start_period,
                args.end_period,
                include_hvfhv=args.include_hvfhv,
                max_periods=args.max_periods,
                dry_run=args.dry_run,
            )
        except ContractError as exc:
            print(f"Erro de contrato: {exc}", file=sys.stderr)
            return EXIT_CONTRACT_ERROR
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return EXIT_BUILD_ERROR if (result["build"] or {}).get("status") == "failed" else 0

    if args.command == "build":
        try:
            result = build(args.period, full_refresh=args.full_refresh)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return result["returncode"]

    if args.command == "ingest":
        if ADAPTERS[args.dataset].PERIODIC != (args.period is not None):
            need = "exige" if ADAPTERS[args.dataset].PERIODIC else "não aceita"
            parser.error(f"{args.dataset} {need} --period")
        try:
            result = ingest(args.dataset, args.period, force=args.force)
        except ContractError as exc:
            # Falha estrutural do arquivo: repetir não adianta (o orquestrador não faz retry).
            print(f"Erro de contrato: {exc}", file=sys.stderr)
            return EXIT_CONTRACT_ERROR
        print(json.dumps(result, ensure_ascii=False))
        return 0

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
