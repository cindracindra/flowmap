#!/usr/bin/env bash
# FlowMap: analyse a Java project and open the interactive viewer.
#
#   ./flowmap.sh <path-to-java-project> [options]
#   ./flowmap.sh --demo
#
# Run with --help for all options.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$REPO_ROOT/demo/petclinic"
ARTIFACTS=(raw_cfg.json full_cfg.json graph_bundle.json topic_cluster.json
  opseq_topic_assignment.json opseq_labels.json topic_operations.json
  phase_gate_decisions.json)
# The subset the viewer reads; this is all the bundled demo ships.
VIEWER_ARTIFACTS=(graph_bundle.json topic_cluster.json topic_operations.json)

usage() {
  cat <<EOF
Usage: ./flowmap.sh <path-to-java-project> [options]
       ./flowmap.sh --demo [--port N]

Analyses the Java source tree, then starts the FlowMap viewer in your browser.
If the project was already analysed, the saved results are reused.

Options:
      --demo            Open the bundled Spring PetClinic results without
                        analysing anything (needs only Node.js)
  -o, --output DIR      Where results are stored (default: runs/<project-name>)
  -p, --provider NAME   LLM provider: groq (default) or together
      --preset NAME     Topic granularity: fine, balanced (default) or coarse
      --reanalyse       Discard saved results for this project and analyse again
      --analyse-only    Run the analysis but do not start the viewer
      --view-only       Skip analysis and open existing results
      --port N          Viewer port (default: 5173)
  -h, --help            Show this help

Examples:
  ./flowmap.sh --demo
  ./flowmap.sh ~/code/spring-petclinic
  ./flowmap.sh ~/code/spring-petclinic --reanalyse --preset fine
  ./flowmap.sh --view-only -o runs/spring-petclinic
EOF
}

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || fail "$1 is not installed. $2"; }

# ---------------------------------------------------------------- arguments
SOURCE_DIR="" OUTPUT_DIR="" PROVIDER="groq" PRESET="balanced" PORT="5173"
REANALYSE=0 ANALYSE=1 VIEW=1 DEMO=0 ANALYSIS_OPTS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output)    OUTPUT_DIR="${2:?missing value for $1}"; ANALYSIS_OPTS=1; shift 2 ;;
    -p|--provider)  PROVIDER="${2:?missing value for $1}"; ANALYSIS_OPTS=1; shift 2 ;;
    --preset)       PRESET="${2:?missing value for $1}"; ANALYSIS_OPTS=1; shift 2 ;;
    --port)         PORT="${2:?missing value for $1}"; shift 2 ;;
    --reanalyse)    REANALYSE=1; ANALYSIS_OPTS=1; shift ;;
    --analyse-only) VIEW=0; ANALYSIS_OPTS=1; shift ;;
    --view-only)    ANALYSE=0; ANALYSIS_OPTS=1; shift ;;
    --demo)         DEMO=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             usage >&2; fail "unknown option: $1" ;;
    *)              [[ -z "$SOURCE_DIR" ]] || fail "only one project path may be given"
                    SOURCE_DIR="$1"; shift ;;
  esac
done

if [[ $DEMO -eq 1 ]]; then
  [[ -z "$SOURCE_DIR" && $ANALYSIS_OPTS -eq 0 ]] \
    || fail "--demo cannot be combined with a project path or analysis options (only --port)"
  OUTPUT_DIR="$DEMO_DIR" ANALYSE=0
  ARTIFACTS=("${VIEWER_ARTIFACTS[@]}")
  for name in "${ARTIFACTS[@]}"; do
    [[ -f "$DEMO_DIR/$name" ]] || fail "demo data is missing: $DEMO_DIR/$name"
  done
  info "Demo mode: showing bundled Spring PetClinic results (no analysis is run)"
elif [[ -n "$SOURCE_DIR" ]]; then
  [[ -d "$SOURCE_DIR" ]] || fail "not a directory: $SOURCE_DIR"
  SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
  [[ -n "$OUTPUT_DIR" ]] || OUTPUT_DIR="$REPO_ROOT/runs/$(basename "$SOURCE_DIR")"
elif [[ $ANALYSE -eq 1 ]]; then
  usage >&2; fail "please give the path to a Java project"
fi
[[ -n "$OUTPUT_DIR" ]] || fail "--view-only needs --output DIR or a project path"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

is_complete() {
  local name
  for name in "${ARTIFACTS[@]}"; do [[ -f "$OUTPUT_DIR/$name" ]] || return 1; done
}

# ----------------------------------------------------------------- analysis
if [[ $ANALYSE -eq 1 ]]; then
  if [[ $REANALYSE -eq 1 ]]; then
    info "Removing previous results in $OUTPUT_DIR"
    for name in "${ARTIFACTS[@]}" telemetry.json; do rm -f "$OUTPUT_DIR/$name"; done
    rm -rf "$OUTPUT_DIR/cpg.bin"
  fi

  if is_complete; then
    info "Reusing saved analysis in $OUTPUT_DIR (pass --reanalyse to run it again)"
  else
    require poetry "See https://python-poetry.org/docs/#installation"
    require java "Install a JDK (17 or newer), e.g. 'brew install openjdk'."
    command -v joern >/dev/null 2>&1 || [[ -x "$HOME/bin/joern/joern-cli/joern" ]] \
      || [[ -x "$HOME/.joern/joern-cli/joern" ]] || [[ -x /opt/joern/joern-cli/joern ]] \
      || fail "Joern is not installed. See https://docs.joern.io/installation"

    case "$PROVIDER" in
      groq)     KEY_NAME=GROQ_API_KEY ;;
      together) KEY_NAME=TOGETHERAI_API_KEY ;;
      *)        fail "unknown provider: $PROVIDER (use groq or together)" ;;
    esac
    if [[ -z "${!KEY_NAME:-}" ]] && ! grep -qs "^$KEY_NAME=." "$REPO_ROOT/.env"; then
      fail "$KEY_NAME is not set. Copy .env.example to .env and add your key."
    fi

    # Leftovers from an interrupted run would make the pipeline refuse to start.
    for name in "${ARTIFACTS[@]}"; do rm -f "$OUTPUT_DIR/$name"; done

    info "Installing Python dependencies (first run only)"
    (cd "$REPO_ROOT" && poetry install --quiet)

    info "Analysing $SOURCE_DIR"
    (cd "$REPO_ROOT" && poetry run python backend/src/flowmap/main.py \
      --source-dir "$SOURCE_DIR" \
      --output-dir "$OUTPUT_DIR" \
      --provider "$PROVIDER" \
      --flowmap-preset "$PRESET")
    info "Analysis saved to $OUTPUT_DIR"
  fi
fi

# ------------------------------------------------------------------- viewer
[[ $VIEW -eq 1 ]] || exit 0
is_complete || fail "no complete analysis in $OUTPUT_DIR. Run without --view-only first."

require npm "Install Node.js 20 or newer from https://nodejs.org"
if [[ ! -d "$REPO_ROOT/frontend/node_modules" ]]; then
  info "Installing frontend dependencies (first run only)"
  npm --prefix "$REPO_ROOT/frontend" ci --silent
fi

info "Starting viewer at http://localhost:$PORT (Ctrl+C to stop)"
cd "$REPO_ROOT/frontend"
FLOWMAP_OUTPUT_DIR="$OUTPUT_DIR" exec npx vite --port "$PORT" --open
