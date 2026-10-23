# 1. Preliminary verification without making changes (Dry-run)
./scripts/release_tag.sh --dry-run

# 2. Create and push the tag for the current version (with interactive confirmation prompt [y/N])
./scripts/release_tag.sh

# Or without manual confirmation (e.g., in CI environments or scripts)
./scripts/release_tag.sh --yes

# Overwrite and re-push existing tag (e.g., after fixing workflow configuration on main)
./scripts/release_tag.sh --force --yes

