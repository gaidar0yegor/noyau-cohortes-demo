#!/usr/bin/env bash
# Démonstrateur du noyau — une seule commande.
#
#   ./demo.sh            construit, démarre sur le port 8090, rejoue les 40 questions, écrit le log
#   ./demo.sh up         construit et démarre seulement
#   ./demo.sh run40      rejoue les 40 questions contre l'instance déjà démarrée
#   ./demo.sh down       arrête et supprime le conteneur
#   ./demo.sh logs       les 40 dernières lignes du conteneur
#
# Sur cette machine le socket Docker appartient à root (docker-socket-proxy en façade) :
# le script préfixe automatiquement par sudo quand l'utilisateur n'a pas l'accès direct.
set -euo pipefail

cd "$(dirname "$0")"

PORT_HOTE="${PORT_HOTE:-8090}"

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker)
else
  echo "Docker n'est pas accessible (ni directement, ni par sudo -n)." >&2
  exit 1
fi

attendre() {
  for _ in $(seq 1 60); do
    if curl -fsS -m 2 -o /dev/null "http://localhost:${PORT_HOTE}/"; then
      return 0
    fi
    sleep 1
  done
  echo "Le démonstrateur ne répond pas sur http://localhost:${PORT_HOTE}/" >&2
  "${DOCKER[@]}" compose logs --tail 40 >&2 || true
  return 1
}

case "${1:-demo}" in
  up)
    "${DOCKER[@]}" compose up -d --build
    attendre
    echo "Démonstrateur prêt : http://localhost:${PORT_HOTE}/"
    ;;
  run40)
    shift || true
    python3 run40.py --base "http://localhost:${PORT_HOTE}" --strict "$@"
    ;;
  down)
    "${DOCKER[@]}" compose down
    ;;
  logs)
    "${DOCKER[@]}" compose logs --tail 40
    ;;
  demo)
    "${DOCKER[@]}" compose up -d --build
    attendre
    echo "Démonstrateur prêt : http://localhost:${PORT_HOTE}/"
    echo "Rejeu des 40 questions du carnet…"
    python3 run40.py --base "http://localhost:${PORT_HOTE}" --strict
    ;;
  *)
    echo "usage: $0 [demo|up|run40|down|logs]" >&2
    exit 2
    ;;
esac
