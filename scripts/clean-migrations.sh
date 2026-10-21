
#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
MODE="${2:---dry-run}"

if [[ ! -d "$ROOT" ]]; then
  echo "Directory not found: $ROOT"
  exit 1
fi

if [[ "$MODE" != "--dry-run" && "$MODE" != "--delete" ]]; then
  echo "Usage: $0 [directory] [--dry-run|--delete]"
  exit 1
fi

echo "Searching for Django migrations in: $ROOT"

mapfile -d '' FILES < <(
  find "$ROOT" \
    -type d \( \
      -name .git -o \
      -name .venv -o \
      -name venv -o \
      -name node_modules -o \
      -name __pycache__ \
    \) -prune -o \
    -type f \
    -path '*/migrations/[0-9]*.py' \
    -print0
)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No migration files found."
  exit 0
fi

printf 'Found %d migration file(s):\n' "${#FILES[@]}"
printf '  %s\n' "${FILES[@]}"

if [[ "$MODE" == "--dry-run" ]]; then
  echo
  echo "Dry run only. No files deleted."
  echo "Run with --delete to remove them."
  exit 0
fi

echo
read -r -p "Delete these migration files? [y/N] " CONFIRM

if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
  echo "Cancelled."
  exit 0
fi

for FILE in "${FILES[@]}"; do
  rm -- "$FILE"
done

echo "Deleted ${#FILES[@]} migration file(s)."
