#!/usr/bin/env bash
# ==============================================================================
# VerifyCraft Release Tag Script
#
# Reads the project version dynamically from pyproject.toml and creates/pushes
# an annotated Git release tag (e.g., v0.1.0) to origin.
#
# Usage:
#   ./scripts/release_tag.sh [OPTIONS]
#
# Options:
#   -y, --yes       Execute without interactive confirmation
#   -d, --dry-run   Perform all validation checks without creating or pushing tag
#   -h, --help      Show this help message
# ==============================================================================

set -euo pipefail

# Ensure script runs from the repository root
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=false
AUTO_CONFIRM=false
FORCE_TAG=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -f|--force)
            FORCE_TAG=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Creates and pushes an annotated release tag to origin based on pyproject.toml version."
            echo ""
            echo "Options:"
            echo "  -y, --yes      Execute without interactive confirmation"
            echo "  -d, --dry-run  Validate release preconditions without creating/pushing tag"
            echo "  -f, --force    Recreate and re-push tag even if it already exists"
            echo "  -h, --help     Show this help message"
            exit 0
            ;;
        *)
            echo "Error: Unknown option '$1'." >&2
            echo "Run '$0 --help' for usage." >&2
            exit 1
            ;;
    esac
done

echo "=== VerifyCraft Release Tag Automation ==="

# 1. Verify required tools are present
for cmd in git python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: Required tool '$cmd' is not installed or not in PATH." >&2
        exit 1
    fi
done

# 2. Extract version from pyproject.toml
if [[ ! -f "pyproject.toml" ]]; then
    echo "Error: pyproject.toml not found in repository root ($REPO_ROOT)." >&2
    exit 1
fi

VERSION=$(python3 -c "
import pathlib, tomllib
data = tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))
print(data.get('project', {}).get('version', ''))
")

if [[ -z "$VERSION" ]]; then
    echo "Error: Could not extract project.version from pyproject.toml." >&2
    exit 1
fi

TAG="v${VERSION}"
MESSAGE="VerifyCraft ${VERSION}"

# Validate tag format against release regex
if ! [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(a[0-9]+|b[0-9]+|rc[0-9]+)?$ ]]; then
    echo "Error: Tag '$TAG' does not conform to release format convention:" >&2
    echo "       Expected format: vMAJOR.MINOR.PATCH[aN|bN|rcN] (e.g. v0.1.0 or v0.1.0a1)" >&2
    exit 1
fi

# 3. Verify synchronization with src/verifycraft/__init__.py
INIT_PY="src/verifycraft/__init__.py"
if [[ -f "$INIT_PY" ]]; then
    INIT_VERSION=$(python3 -c "
import pathlib
lines = pathlib.Path('$INIT_PY').read_text(encoding='utf-8').splitlines()
for line in lines:
    if line.startswith('__version__'):
        print(line.split('=', 1)[1].strip().strip('\"\''))
        break
")
    if [[ "$VERSION" != "$INIT_VERSION" ]]; then
        echo "Error: Version mismatch between pyproject.toml ('$VERSION') and $INIT_PY ('$INIT_VERSION')." >&2
        exit 1
    fi
fi

echo "  Target Version: $VERSION"
echo "  Git Tag Name:   $TAG"
echo "  Tag Message:    $MESSAGE"

# 4. Check for clean working tree
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: Git working tree is dirty or has untracked changes." >&2
    echo "       Please commit or stash your changes before releasing:" >&2
    git status -s >&2
    exit 1
fi

# 5. Check current branch is 'main'
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo "Error: Release tags must be created from branch 'main' (current branch: '$CURRENT_BRANCH')." >&2
    exit 1
fi

# 6. Verify synchronization with origin/main
echo "  Fetching latest state from origin/main..."
git fetch origin main --quiet

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/main)

if [[ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]]; then
    echo "Error: Local branch 'main' ($LOCAL_HEAD) is not in sync with origin/main ($REMOTE_HEAD)." >&2
    echo "       Please run 'git pull --ff-only' or push local commits before tagging." >&2
    exit 1
fi

# 7. Check if tag already exists locally
if git rev-parse "$TAG" >/dev/null 2>&1; then
    EXISTING_COMMIT=$(git rev-parse "$TAG^{commit}")
    if [[ "$FORCE_TAG" == true ]]; then
        echo "  Notice: Local tag '$TAG' exists ($EXISTING_COMMIT); will be overwritten (--force)."
    else
        echo "Error: Tag '$TAG' already exists locally pointing to commit $EXISTING_COMMIT." >&2
        echo "       Use --force if you need to recreate and re-push the tag." >&2
        exit 1
    fi
fi

# 8. Check if tag already exists remotely
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
    if [[ "$FORCE_TAG" == true ]]; then
        echo "  Notice: Remote tag '$TAG' exists on origin; will be overwritten (--force)."
    else
        echo "Error: Tag '$TAG' already exists on remote 'origin'." >&2
        echo "       Use --force if you need to recreate and re-push the tag." >&2
        exit 1
    fi
fi

echo "  Pre-release validations passed successfully."

if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo "[DRY RUN] Would execute:"
    if [[ "$FORCE_TAG" == true ]]; then
        echo "  git tag -f -a \"$TAG\" -m \"$MESSAGE\""
        echo "  git push origin \"refs/tags/$TAG\" --force"
    else
        echo "  git tag -a \"$TAG\" -m \"$MESSAGE\""
        echo "  git push origin \"$TAG\""
    fi
    echo ""
    echo "[DRY RUN] No changes were made."
    exit 0
fi

# 9. Prompt for confirmation unless -y/--yes specified
if [[ "$AUTO_CONFIRM" != true ]]; then
    echo ""
    read -r -p "Are you sure you want to create and push tag '$TAG' to origin? [y/N]: " CONFIRM
    case "$CONFIRM" in
        [yY]|[yY][eE][sS])
            ;;
        *)
            echo "Release aborted by user."
            exit 0
            ;;
    esac
fi

# 10. Create and push tag
if [[ "$FORCE_TAG" == true ]]; then
    echo "Recreating annotated tag '$TAG'..."
    git tag -f -a "$TAG" -m "$MESSAGE"
    echo "Force-pushing tag '$TAG' to origin..."
    git push origin "refs/tags/$TAG" --force
else
    echo "Creating annotated tag '$TAG'..."
    git tag -a "$TAG" -m "$MESSAGE"
    echo "Pushing tag '$TAG' to origin..."
    git push origin "$TAG"
fi

echo ""
echo "=== Release Tag Successfully Pushed ==="
echo "Tag '$TAG' is now published on GitHub."
echo "GitHub Actions will trigger the 'Release to PyPI' workflow automatically."
echo "Track progress at: https://github.com/apply-the/verifycraft/actions"
