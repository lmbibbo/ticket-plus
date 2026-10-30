#!/usr/bin/env bash
set -u
cd "$(dirname "$0")" || exit 1

BRANCH="${1:-}"
CURRENT_BRANCH="$(git branch --show-current 2>/dev/null)"
PR_NUMBER=""
PR_STATE=""

usage() {
    echo "Uso: $(basename "$0") nombre-de-la-rama"
    echo "Ejemplo: $(basename "$0") feature/remove_untagged_photos"
    exit 1
}

failed() {
    echo "El proceso se detuvo por un error. Revisa el mensaje anterior; la rama no se elimina salvo que el merge haya tenido exito."
    exit 1
}

done_ok() {
    exit 0
}

[ -n "$BRANCH" ] || usage

case "$BRANCH" in
    *[!a-zA-Z0-9._/-]*|-*)
        echo "Error: \"$BRANCH\" no es un nombre de rama valido."
        failed
        ;;
esac

EXISTING_PR="$(gh pr list --head "$BRANCH" --state open --json number --jq '.[0].number // empty' 2>/dev/null || true)"

if [ "$CURRENT_BRANCH" != "main" ] && [ -z "$EXISTING_PR" ]; then
    echo "Error: ejecuta este script estando en main. Rama actual: ${CURRENT_BRANCH:-desconocida}"
    failed
fi

DIRTY_TREE="$(git status --porcelain --untracked-files=all | grep -v "^?? publicar-pr.sh$" || true)"
if [ -n "$DIRTY_TREE" ]; then
    echo "Error: hay cambios sin commit. Guarda o descarta los cambios antes de continuar."
    failed
fi

if [ -n "$EXISTING_PR" ]; then
    echo "Reutilizando el pull request #$EXISTING_PR de $BRANCH."
else
    echo "Creando la rama $BRANCH desde main..."
    git switch -c "$BRANCH" || failed

    echo "Publicando la rama..."
    git push -u origin "$BRANCH" || failed

    echo "Creando el pull request contra main..."
    gh pr create --base main --head "$BRANCH" || failed
fi

echo
echo "Los checks requeridos se esperan automaticamente antes del merge."
read -r -p "Enter para esperar los checks y mergear; N para dejarlo pendiente: " ANSWER
case "$ANSWER" in
    "" | [Ss]*) ;;
    *) echo; echo "PR creado. No se mergeo ni se elimino la rama."; done_ok ;;
esac

echo "Esperando a que los checks del PR terminen (maximo 30 minutos)..."
if ! timeout 1800 gh pr checks "$BRANCH" --watch --fail-fast; then
    echo "Error: los checks no pasaron o se agoto el tiempo de espera."
    failed
fi
echo "Todos los checks pasaron."

echo "Cambiando a main..."
git switch main || failed

PR_NUMBER="${EXISTING_PR:-}"
if [ -z "$PR_NUMBER" ]; then
    PR_NUMBER="$(gh pr view "$BRANCH" --json number --jq .number)"
fi
[ -n "$PR_NUMBER" ] || failed

echo "Habilitando auto-merge del PR..."
gh pr merge "$PR_NUMBER" --merge --auto --delete-branch || failed

PR_STATE="$(gh pr view "$PR_NUMBER" --json state --jq .state)"
if [ "$PR_STATE" != "MERGED" ]; then
    echo "Auto-merge habilitado. GitHub mergeara el PR cuando se cumplan los requisitos."
    echo "La rama se conserva hasta que el PR figure como MERGED."
    echo "Entonces actualiza main y elimina la rama con:"
    echo "  git pull origin main"
    echo "  git branch -d \"$BRANCH\""
    echo "  git push origin --delete \"$BRANCH\""
    done_ok
fi

echo "Actualizando main..."
git pull origin main || failed

echo "Listo: PR mergeado y ramas eliminadas."
done_ok
