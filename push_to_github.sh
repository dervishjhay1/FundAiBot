#!/bin/bash
# Push all bot files to GitHub
# Only Railway should run the production bot. This script just syncs code.
set -e

cd "$(dirname "$0")"

git config user.email "agent@replit.com"
git config user.name "Replit Agent"

# Stage everything
git add -A

COMMIT_MSG="${1:-Added premium sticky announcement overlay, image retouching, multi-announcement navigator (v2.5.0)}"

# Commit
git commit -m "$COMMIT_MSG" || echo "Nothing new to commit."

# Push to remote
git push origin main
echo "Done: pushed to https://github.com/dervishjhay1/FundAiBot"
echo "Railway will auto-deploy. Only Railway runs polling — Replit stays silent."
