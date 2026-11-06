"""Tipos e utilitários compartilhados pelos adaptadores."""

import re
from datetime import datetime

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ContractError(ValueError):
    """Falha estrutural: o arquivo não atende ao contrato e não pode ser carregado."""


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """Início (inclusivo) e fim (exclusivo) da competência, como datas/horas locais ingênuas."""
    if not PERIOD_RE.match(period):
        raise ValueError(f"Competência inválida (esperado AAAA-MM): {period!r}")
    year, month = int(period[:4]), int(period[5:])
    return datetime(year, month, 1), datetime(year + month // 12, month % 12 + 1, 1)
