#!/usr/bin/env bash
# attach-kit.sh REQUESTED_TAG KIT_GLOB ASSET_MARK - attach a built kit to the GitHub release it belongs to.
#   REQUESTED_TAG  the workflow_dispatch input (may be empty)
#   KIT_GLOB       the zip the build wrote, e.g. dist/gea-program-*-win64.zip
#   ASSET_MARK     the substring that marks this platform's asset in the release, e.g. -win64.zip
# The rule: a tag run attaches to that tag and replaces; a manual run with a tag attaches to that tag and
# replaces; a run on main attaches to the release of the version in pyproject.toml (creating the release when
# the tag exists but a failed tag run left none), and only when that
# release has no kit for this platform yet. A kit whose version does not match the tag is never attached.
set -euo pipefail
requested="${1:-}"; kit_glob="${2:?kit glob}"; mark="${3:?asset mark}"
kit=$(ls $kit_glob 2>/dev/null | head -n1)
[ -n "$kit" ] || { echo "no kit matches $kit_glob"; exit 1; }
ver=$(python -c "import re,sys; print(re.search(r'^version = \"([^\"]+)\"', open('pyproject.toml', encoding='utf-8').read(), re.M).group(1))")
replace=0
if [ -n "$requested" ]; then
  tag="$requested"; replace=1; why="requested by hand"
elif [[ "${GITHUB_REF:-}" == refs/tags/v* ]]; then
  tag="${GITHUB_REF#refs/tags/}"; replace=1; why="this run is the tag's own"
else
  tag="v$ver"; why="a push to main; the release of the version in pyproject.toml"
fi
echo "kit $kit (version $ver) -> release $tag ($why)"
if [ "$tag" != "v$ver" ]; then
  echo "the kit's version $ver is not the tag's ($tag) - not attached"; exit 0
fi
if ! gh release view "$tag" >/dev/null 2>&1; then
  # A tag run creates the release. A run on main may create it too, but only when the tag itself already
  # exists in the repository (the ship made it) - a fix pushed after a failed tag run must still be able to
  # put the kits on their release. The release notes are SHIP_MESSAGE.txt when it is that tag's own message.
  if [ "$replace" = 1 ] || [ -n "$(git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null)" ]; then
    if [[ "$(head -n1 SHIP_MESSAGE.txt | tr -d '\r')" == "$tag"* ]]; then
      gh release create "$tag" --title "$tag" --notes-file SHIP_MESSAGE.txt
    else
      gh release create "$tag" --title "$tag" --notes "Release $tag. See CHANGELOG.md."
    fi
    echo "created release $tag"
  else
    echo "no release $tag exists and no tag $tag in the repository - not attached (a ship creates the tag)"; exit 0
  fi
fi
has_kit() { gh release view "$tag" --json assets -q '.assets[].name' | grep -q -- "$mark"; }
if [ "$replace" = 0 ]; then
  if has_kit; then
    echo "release $tag already has a kit matching '$mark' - left as it is"; exit 0
  fi
  # A push to main on a shipped commit runs beside the tag's own run of the same commit, and the two reach this
  # step within seconds of each other (v0.13.0: the main run's upload collided with the tag run's and the main
  # run went red with the kit already on the release). A main run attaches without --clobber, and an upload
  # that fails because the asset appeared meanwhile is the tag run's work, not a failure.
  if gh release upload "$tag" "$kit"; then
    echo "attached $(basename "$kit") to $tag"; exit 0
  fi
  sleep 30
  if has_kit; then
    echo "release $tag received a kit matching '$mark' from the tag's own run meanwhile - left as it is"; exit 0
  fi
  echo "upload failed and no kit matching '$mark' is on release $tag"; exit 1
fi
gh release upload "$tag" "$kit" --clobber
echo "attached $(basename "$kit") to $tag"
