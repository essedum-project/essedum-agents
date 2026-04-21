#!/usr/bin/env bash
# patch-builder-kit-2.sh
# Patches the running builderk-service-2 container with the new /deploy and /apps endpoints.
# Usage: bash patch-builder-kit-2.sh
set -euo pipefail

NAMESPACE="aipns"
DEPLOY="builderk-service-2"
ADDITIONS_FILE="$(dirname "$0")/app_additions.py"

echo "[1/4] Checking pod is running..."
POD=$(kubectl get pod -n "$NAMESPACE" -l app=builderk-2 -o jsonpath='{.items[0].metadata.name}')
echo "      Pod: $POD"

echo "[2/4] Checking if /deploy route already patched..."
if kubectl exec -n "$NAMESPACE" "$POD" -- grep -q 'def deploy_session' /app/app.py 2>/dev/null; then
  echo "      Already patched. Skipping."
  exit 0
fi

echo "[3/4] Appending additions to /app/app.py in container..."
# Copy the additions file in
kubectl cp "$ADDITIONS_FILE" "$NAMESPACE/$POD:/tmp/app_additions.py"

# Write a helper script to the pod and run it
kubectl exec -n "$NAMESPACE" "$POD" -- bash -c 'cat > /tmp/patch.py << '"'"'PYEOF'"'"'
import re

with open("/app/app.py", "r") as f:
    content = f.read()

# Strip the __main__ guard at end so we can re-add it after additions
content = re.sub(r"\nif __name__.*", "", content, flags=re.DOTALL)

with open("/tmp/app_additions.py", "r") as f:
    additions = f.read()

with open("/app/app.py", "w") as f:
    f.write(content.rstrip() + "\n\n")
    f.write(additions + "\n\n")
    f.write("if __name__ == \"__main__\":\n")
    f.write("    socketio.run(app, host=\"0.0.0.0\", port=5000)\n")

print("Patch applied successfully.")
PYEOF
python3 /tmp/patch.py'

echo "[4/4] Restarting deployment to apply changes..."
kubectl rollout restart deployment/"$DEPLOY" -n "$NAMESPACE"
kubectl rollout status deployment/"$DEPLOY" -n "$NAMESPACE" --timeout=90s

echo ""
echo "Done! /deploy endpoint is now available on builderk-service-2."
echo "Test: kubectl exec -n $NAMESPACE deploy/$DEPLOY -- curl -s -X POST http://localhost:5000/deploy -H 'Content-Type: application/json' -d '{\"session_id\":\"test\"}'"
