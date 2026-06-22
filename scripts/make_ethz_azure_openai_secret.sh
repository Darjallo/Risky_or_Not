#!/usr/bin/env bash
set -euo pipefail

OUTPUT_PATH="../k8s/ethz-azure-openai-secrets.yaml"
SECRET_NAME="ethz-azure-openai-secrets"

API_KEY="${1:-}"

if [ -z "$API_KEY" ]; then
  echo -n "Enter Azure OpenAI API key: "
  read -rs API_KEY
  echo
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"

ENCODED_KEY=$(printf '%s' "$API_KEY" | base64 | tr -d '\n')

cat > "$OUTPUT_PATH" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${SECRET_NAME}
type: Opaque
data:
  api_key: ${ENCODED_KEY}
EOF

echo "Secret manifest written to ${OUTPUT_PATH}"
echo "!!! Do not commit it. Make sure filename ends on -secrets.yaml !!!"
