#!/usr/bin/env bash
# Збирає мультиархітектурний образ Мольфара (x86_64 + ARM64) через docker buildx.
#
#   IMAGE=registry.example.com/molfar:1.0 ./docker/build-multiarch.sh            # зібрати й надіслати в реєстр
#   ./docker/build-multiarch.sh --tar                                             # без реєстру: molfar-amd64.tar, molfar-arm64.tar
#
# Змінні: IMAGE (за замовчуванням molfar:latest), PLATFORMS (linux/amd64,linux/arm64), EXTRA_TOOLS (1)
# На ВМ із .tar:  docker load -i molfar-arm64.tar && docker compose up -d   (без --build)
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-molfar:latest}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
EXTRA_TOOLS="${EXTRA_TOOLS:-1}"
BUILDER=molfar-builder

# Емуляція інших архітектур (QEMU), якщо ще не встановлена
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker run --privileged --rm tonistiigi/binfmt --install arm64,amd64 >/dev/null
  docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi

if [ "${1:-}" = "--tar" ]; then
  IFS=, read -ra P <<<"$PLATFORMS"
  for p in "${P[@]}"; do
    arch=${p#linux/}; arch=${arch//\//}
    echo "==> $p → molfar-$arch.tar"
    docker buildx build --builder "$BUILDER" --platform "$p" --build-arg EXTRA_TOOLS="$EXTRA_TOOLS" \
      -t "$IMAGE" --output "type=docker,dest=molfar-$arch.tar" .
  done
else
  echo "==> $PLATFORMS → $IMAGE (push)"
  docker buildx build --builder "$BUILDER" --platform "$PLATFORMS" --build-arg EXTRA_TOOLS="$EXTRA_TOOLS" \
    -t "$IMAGE" --push .
fi
