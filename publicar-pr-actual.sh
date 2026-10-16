#!/usr/bin/env bash
# PROVISORIO: crea el PR de la rama actual contra main, lo mergea y limpia el repo.
# Uso: ./publicar-pr-actual.sh ["titulo del PR"] ["cuerpo del PR"]
set -u
cd "$(dirname "$0")" || exit 1

BRANCH="$(git branch --show-current 2>/dev/null)"
TITLE="${1:-}"
BODY="${2:-}"
PR_NUMBER=""
STATE=""

fail() {
    echo "Error: $1"
    exit 1
}

[ -n "$BRANCH" ] || fail "no se pudo determinar la rama actual"
[ "$BRANCH" != "main" ] || fail "estas en main; ejecuta este script desde la rama con el trabajo"

DIRTY="$(git status --porcelain --untracked-files=all | grep -vE '^\?\? publicar-pr(-actual)?\.sh$' || true)"
if [ -n "$DIRTY" ]; then
    echo "$DIRTY"
    fail "hay cambios sin commit; hace commit o stash antes de continuar"
fi

git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 \
    || fail "la rama $BRANCH no tiene upstream; ejecuta: git push -u origin $BRANCH"

UNPUSHED="$(git rev-list --count '@{u}..HEAD')"
[ "$UNPUSHED" -eq 0 ] || fail "hay $UNPUSHED commits sin pushear; ejecuta: git push"

if PR_NUMBER="$(gh pr view "$BRANCH" --json number --jq .number 2>/dev/null)" && [ -n "$PR_NUMBER" ]; then
    echo "El PR #$PR_NUMBER ya existe; no se crea otro."
else
    [ -n "$TITLE" ] || TITLE="$(git log -1 --pretty=%s)"
    [ -n "$BODY" ] || BODY="Commits de la rama $BRANCH hacia main."
    echo "Creando PR $BRANCH -> main..."
    gh pr create --base main --head "$BRANCH" --title "$TITLE" --body "$BODY" \
        || fail "no se pudo crear el PR (verifica que gh este instalado y autenticado)"
    PR_NUMBER="$(gh pr view "$BRANCH" --json number --jq .number)"
    [ -n "$PR_NUMBER" ] || fail "no se pudo obtener el numero del PR"
fi

echo
echo "PR: $(gh pr view "$PR_NUMBER" --json url --jq .url)"
read -r -p "Enter para mergear ahora; N para dejarlo pendiente: " ANSWER
case "$ANSWER" in
    "" | [Ss]*) ;;
    *) echo "PR #$PR_NUMBER creado. No se mergeo ni se elimino la rama."; exit 0 ;;
esac

echo "Habilitando merge del PR #$PR_NUMBER..."
if ! gh pr merge "$PR_NUMBER" --merge --auto --delete-branch 2>/dev/null; then
    echo "Auto-merge no disponible; intentando merge directo..."
    gh pr merge "$PR_NUMBER" --merge --delete-branch || fail "no se pudo mergear el PR"
fi

echo "Esperando a que el PR quede MERGED (max 10 min)..."
STATE="OPEN"
i=0
while [ "$i" -lt 60 ]; do
    STATE="$(gh pr view "$PR_NUMBER" --json state --jq .state)"
    [ "$STATE" = "MERGED" ] && break
    sleep 10
    i=$((i + 1))
done

if [ "$STATE" != "MERGED" ]; then
    echo "El PR #$PR_NUMBER todavia no es MERGED (auto-merge habilitado)."
    echo "Cuando figure mergeado, limpia el repo con:"
    echo "  git switch main && git pull origin main"
    echo "  git branch -d \"$BRANCH\""
    echo "  git push origin --delete \"$BRANCH\""
    echo "  git fetch --prune"
    exit 0
fi

echo "Limpiando el repo..."
git switch main || fail "no se pudo cambiar a main"
git pull origin main || fail "fallo git pull origin main"
git branch -d "$BRANCH" 2>/dev/null || true
git push origin --delete "$BRANCH" 2>/dev/null || true
git fetch --prune || true

echo "Listo: PR #$PR_NUMBER mergeado, main actualizado y rama $BRANCH eliminada."
