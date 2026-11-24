#!/usr/bin/env bash
# Walks through the main behaviour of the API against a running server:
# create a product, place an order, retry it with the same key, hit the stock limit,
# and cancel the order. Safe to run repeatedly: every run uses fresh names and keys.
#
#   BASE_URL=http://localhost:8080 scripts/demo.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
RUN_ID="$(date +%s)"
KEY="demo-${RUN_ID}"

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
call() { # call <method> <path> [curl args...]; prints status and body
  local method="$1" path="$2"; shift 2
  curl -s -w '\n  -> HTTP %{http_code}\n' -X "$method" "${BASE_URL}${path}" "$@"
}

step "1. Create a product with 2 units in stock"
PRODUCT=$(call POST /products -H 'content-type: application/json' \
  -d "{\"name\":\"Demo mug ${RUN_ID}\",\"price_cents\":900,\"stock\":2}")
echo "$PRODUCT"
PRODUCT_ID=$(printf '%s' "$PRODUCT" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read().rsplit("\n  ->",1)[0])["id"])')

step "2. Place an order for 1 unit with Idempotency-Key ${KEY}"
ORDER=$(call POST /orders -H 'content-type: application/json' -H "Idempotency-Key: ${KEY}" \
  -d "{\"product_id\":${PRODUCT_ID},\"quantity\":1}")
echo "$ORDER"
ORDER_ID=$(printf '%s' "$ORDER" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read().rsplit("\n  ->",1)[0])["id"])')

step "3. Retry the same request with the same key (network timeout simulation)"
call POST /orders -H 'content-type: application/json' -H "Idempotency-Key: ${KEY}" -i \
  -d "{\"product_id\":${PRODUCT_ID},\"quantity\":1}" | grep -Ei 'idempotent-replayed|HTTP/|"id"' || true

step "4. Check stock: one unit should be reserved, one left"
call GET "/products/${PRODUCT_ID}"

step "5. Ask for 2 more units when only 1 is left (expect 409)"
call POST /orders -H 'content-type: application/json' -H "Idempotency-Key: ${KEY}-greedy" \
  -d "{\"product_id\":${PRODUCT_ID},\"quantity\":2}"

step "6. Cancel the order: stock goes back to 2"
call POST "/orders/${ORDER_ID}/cancel"
call GET "/products/${PRODUCT_ID}"

step "7. Cancel again: safe, and stock is not released twice"
call POST "/orders/${ORDER_ID}/cancel"
call GET "/products/${PRODUCT_ID}"

printf '\nDemo finished. Interactive docs: %s/docs\n' "$BASE_URL"
