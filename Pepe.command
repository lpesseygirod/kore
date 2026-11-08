#!/bin/zsh
# Double-click to start Pepe on this Mac: his voice (press Enter, wait for the "tin", speak) and his web page
# (http://localhost:8080). Close this window, or type /fin, to stop him.
cd "${0:A:h}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
DB="${KORE_DB:-data/kore.db}"
mkdir -p data
echo "Arrancando Pepe…"
uv run --env-file .env --group live python -m kore.web --db "$DB" serve --port 8080 > data/web.log 2>&1 &
WEB=$!
trap 'kill $WEB 2>/dev/null' EXIT INT TERM
uv run --env-file .env --group live python -m kore.home --db "$DB" --voz
