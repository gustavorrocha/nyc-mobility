"""`python -m dashboard`: sobe o NYC Mobility Explorer."""

import os

from dashboard.app import app

# ponytail: servidor embutido do Dash/Flask, suficiente para uso local; trocar por gunicorn se
# houver acesso concorrente real.
app.run(
    host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
    port=int(os.environ.get("DASHBOARD_PORT", "8050")),
    debug=os.environ.get("DASHBOARD_DEBUG") == "1",
)
