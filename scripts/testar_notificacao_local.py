"""Testa alerta e recuperação usando apenas Alertmanager e Mailpit locais."""

import json
import time
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen
from uuid import uuid4

ALERTMANAGER = "http://127.0.0.1:9093/api/v2/alerts"
MAILPIT = "http://127.0.0.1:8025/api/v1/messages"


def post(alert):
    request = Request(
        ALERTMANAGER,
        data=json.dumps([alert]).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Alertmanager retornou HTTP {response.status}")


def wait_for(subject, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with urlopen(MAILPIT, timeout=10) as response:
            messages = json.load(response).get("messages", [])
        if any(subject in item.get("Subject", "") for item in messages):
            print(f"Recebido: {subject}", flush=True)
            return
        time.sleep(5)
    raise TimeoutError(f"Mensagem não recebida: {subject}")


def main():
    name = "TesteLocal" + uuid4().hex[:12]
    now = datetime.now(UTC)
    alert = {
        "labels": {"alertname": name, "severity": "info"},
        "annotations": {"summary": "Teste local de alerta e recuperação"},
        "startsAt": now.isoformat(),
        "endsAt": (now + timedelta(minutes=10)).isoformat(),
    }
    try:
        post(alert)
        print("Aguardando alerta ativo (agrupamento: 30 segundos)...", flush=True)
        wait_for(f"[FIRING:1] {name}", timeout=90)
    finally:
        alert["endsAt"] = datetime.now(UTC).isoformat()
        post(alert)
    print("Aguardando recuperação (próximo intervalo do grupo: até 5 minutos)...", flush=True)
    wait_for(f"[RESOLVED] {name}", timeout=360)


if __name__ == "__main__":
    main()
