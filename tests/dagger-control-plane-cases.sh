#!/usr/bin/env bash
# Behavioral cases for the Dagger-only execution-plane policy.
#
# Each fixture is a complete synthetic repository. The suite executes the real
# policy scanner and asserts its exit contract; it never greps scanner source.
set -uo pipefail

suite_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scanner="$suite_root/tests/lib/dagger-control-plane.rb"
runner="$suite_root/tests/dagger-control-plane.sh"
failures=0

report() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

fixtures="$(mktemp -d)"
trap 'rm -rf "${fixtures:-}"' EXIT

write_workflow() {
  local repo="$1" workflow="$2"
  local path="$fixtures/$repo/.github/workflows/$workflow"
  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

write_python_module() {
  local repo="$1"
  local root="$fixtures/$repo"
  mkdir -p "$root/.dagger/src/demo"
  printf '%s\n' '{"name":"demo","engineVersion":"v0.21.8","sdk":{"source":"python"},"source":".dagger"}' > "$root/dagger.json"
  cat > "$root/.dagger/src/demo/main.py"
}

write_typescript_module() {
  local repo="$1"
  local root="$fixtures/$repo"
  mkdir -p "$root/dagger/src"
  printf '%s\n' '{"name":"demo","engineVersion":"v0.21.8","sdk":{"source":"typescript"},"source":"dagger"}' > "$root/dagger.json"
  cat > "$root/dagger/src/index.ts"
}

write_bridge_config() {
  local repo="$1"
  mkdir -p "$fixtures/$repo/.github"
  cat > "$fixtures/$repo/.github/dagger-control-plane.yml"
}

copy_thin_repo() {
  local repo="$1"
  mkdir -p "$fixtures/$repo/.github/workflows"
  cp "$fixtures/thin/.github/workflows/dagger.yml" "$fixtures/$repo/.github/workflows/dagger.yml"
  cp -R "$fixtures/thin/.dagger" "$fixtures/$repo/.dagger"
  cp "$fixtures/thin/dagger.json" "$fixtures/$repo/dagger.json"
}

write_metadata() {
  local repo="$1" name="$2"
  mkdir -p "$fixtures/$repo/.control-plane"
  cat > "$fixtures/$repo/.control-plane/$name.json"
}

scan() {
  local repo="$1"
  shift
  ruby "$scanner" --repo "$repo" --path "$fixtures/$repo" "$@"
}

expect_exit() {
  local expected="$1" repo="$2" description="$3"
  shift 3
  local actual=0
  scan "$repo" "$@" >/dev/null 2>&1 || actual=$?
  [[ "$actual" -eq "$expected" ]] ||
    report "$description (exit $actual, expected $expected)"
}

expect_command_exit() {
  local expected="$1" description="$2"
  shift 2
  local actual=0
  "$@" >/dev/null 2>&1 || actual=$?
  [[ "$actual" -eq "$expected" ]] ||
    report "$description (exit $actual, expected $expected)"
}

expect_scan_output() {
  local repo="$1" pattern="$2" description="$3"
  local output
  shift 3
  output="$(scan "$repo" "$@" 2>/dev/null)" || true
  grep -Eq "$pattern" <<< "$output" || report "$description"
}

expect_scan_stderr() {
  local repo="$1" pattern="$2" description="$3"
  shift 3
  local output
  output="$(scan "$repo" "$@" 2>&1 >/dev/null)" || true
  grep -Eq "$pattern" <<< "$output" || report "$description"
}

first_fingerprint() {
  local repo="$1"
  scan "$repo" 2>/dev/null | awk -F'\t' 'NF >= 5 { print $4; exit }'
}

write_workflow thin dagger.yml <<'YAML'
name: Dagger
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  dagger:
    name: Dagger
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with:
          version: "0.21.8"
          verb: check
YAML

write_python_module thin <<'PYTHON'
from typing import Annotated

from dagger import DefaultPath, Directory, field, object_type


@object_type
class Demo:
    source: Annotated[Directory, DefaultPath("/")] = field()
PYTHON

write_workflow inline ci.yml <<'YAML'
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - run: uv run poe gate
YAML

mkdir -p "$fixtures/allowlisted/.github/workflows"
cp "$fixtures/inline/.github/workflows/ci.yml" "$fixtures/allowlisted/.github/workflows/ci.yml"
cp -R "$fixtures/thin/.dagger" "$fixtures/allowlisted/.dagger"
cp "$fixtures/thin/dagger.json" "$fixtures/allowlisted/dagger.json"

write_workflow reusable ci.yml <<'YAML'
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  gate:
    uses: hseshadr/ci/.github/workflows/python-gate.yml@8166345c9355dde54c12fa95d0457c4ea97d3e64
YAML

write_workflow helper ci.yml <<'YAML'
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: check}
  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e
YAML

write_workflow extra-shell dagger.yml <<'YAML'
name: Dagger
on: {pull_request: null}
permissions: {contents: read}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: check}
      - run: dagger check extra
YAML

write_workflow mutable dagger.yml <<'YAML'
name: Dagger
on: {pull_request: null}
permissions: {contents: read}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@v8
        with: {version: latest, verb: check}
YAML

write_workflow credentials dagger.yml <<'YAML'
name: Dagger
on: {pull_request: null}
permissions: {contents: read}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: check}
YAML

write_workflow unsafe-pr dagger.yml <<'YAML'
name: Dagger
on: {pull_request: null}
permissions:
  contents: read
  id-token: write
jobs:
  dagger:
    runs-on: ubuntu-latest
    env:
      DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: check}
YAML

mkdir -p "$fixtures/empty"

mkdir -p "$fixtures/no-module/.github/workflows"
cp "$fixtures/thin/.github/workflows/dagger.yml" "$fixtures/no-module/.github/workflows/dagger.yml"

mkdir -p "$fixtures/implicit/.github/workflows"
cp "$fixtures/thin/.github/workflows/dagger.yml" "$fixtures/implicit/.github/workflows/dagger.yml"
write_python_module implicit <<'PYTHON'
from dagger import dag, function, object_type


@object_type
class Demo:
    @function
    def check(self):
        return dag.current_workspace().directory("/")
PYTHON

mkdir -p "$fixtures/string-secret/.github/workflows"
cp "$fixtures/thin/.github/workflows/dagger.yml" "$fixtures/string-secret/.github/workflows/dagger.yml"
write_python_module string-secret <<'PYTHON'
from typing import Annotated

from dagger import DefaultPath, Directory, field, function, object_type


@object_type
class Demo:
    source: Annotated[Directory, DefaultPath("/")] = field()

    @function
    def deploy(self, cloudflare_api_token: str):
        return self.source
PYTHON

mkdir -p "$fixtures/typescript/.github/workflows"
cp "$fixtures/thin/.github/workflows/dagger.yml" "$fixtures/typescript/.github/workflows/dagger.yml"
write_typescript_module typescript <<'TYPESCRIPT'
import { Directory, Workspace, object } from "@dagger.io/dagger"

@object()
class Demo {
  private source: Directory

  constructor(workspace: Workspace) {
    this.source = workspace.directory("/")
  }
}
TYPESCRIPT

copy_thin_repo generated-sdk
mkdir -p "$fixtures/generated-sdk/.dagger/sdk"
cat > "$fixtures/generated-sdk/.dagger/sdk/runtime.py" <<'PYTHON'
def generated_runtime(client):
    return client.current_workspace()
PYTHON

write_workflow publisher publish.yml <<'YAML'
name: Publish
on:
  push:
    tags: ["v*"]
permissions: {contents: read}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: call, args: "release"}
  publish:
    needs: dagger
    environment: pypi-release
    permissions:
      contents: read
      id-token: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093
      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33
        with: {attestations: true}
YAML
cp -R "$fixtures/thin/.dagger" "$fixtures/publisher/.dagger"
cp "$fixtures/thin/dagger.json" "$fixtures/publisher/dagger.json"
write_bridge_config publisher <<'YAML'
bridges:
  - workflow: publish.yml
    job: publish
    kind: pypi-publisher
YAML

cp -R "$fixtures/publisher" "$fixtures/unsafe-publisher"
write_bridge_config unsafe-publisher <<'YAML'
bridges:
  - workflow: publish.yml
    job: publish
    kind: pypi-publisher
YAML
ruby -0pi -e 'sub("    steps:\n      - uses: actions/download-artifact", "    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n      - uses: actions/download-artifact")' \
  "$fixtures/unsafe-publisher/.github/workflows/publish.yml"

copy_thin_repo global-write
ruby -0pi -e 'sub("  pull_request:\n", ""); sub("contents: read", "contents: write")' \
  "$fixtures/global-write/.github/workflows/dagger.yml"

write_workflow array-pr dagger.yml <<'YAML'
name: Dagger
on: [pull_request]
permissions: {contents: write}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: check}
YAML
cp -R "$fixtures/thin/.dagger" "$fixtures/array-pr/.dagger"
cp "$fixtures/thin/dagger.json" "$fixtures/array-pr/dagger.json"

write_workflow no-jobs empty.yml <<'YAML'
name: Empty
on: {pull_request: null}
permissions: {contents: read}
jobs: {}
YAML
cp -R "$fixtures/thin/.dagger" "$fixtures/no-jobs/.dagger"
cp "$fixtures/thin/dagger.json" "$fixtures/no-jobs/dagger.json"

write_workflow metadata metadata.yml <<'YAML'
name: Metadata
on: {push: {branches: [main]}}
permissions: {contents: read}
jobs:
  dagger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: check}
  project:
    needs: dagger
    permissions:
      contents: read
      checks: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd
        with:
          script: core.notice("Dagger passed")
YAML
cp -R "$fixtures/thin/.dagger" "$fixtures/metadata/.dagger"
cp "$fixtures/thin/dagger.json" "$fixtures/metadata/dagger.json"
write_bridge_config metadata <<'YAML'
bridges:
  - workflow: metadata.yml
    job: project
    kind: github-metadata
YAML

copy_thin_repo protected
write_metadata protected branch-protection <<'JSON'
{"required_status_checks":{"checks":[{"context":"Dagger","app_id":15368}]}}
JSON

copy_thin_repo protected-graphql
write_metadata protected-graphql branch-protection <<'JSON'
{"data":{"repository":{"branchProtectionRules":{"nodes":[{"pattern":"main","requiresStatusChecks":true,"requiredStatusCheckContexts":["Dagger"]}]}}}}
JSON

copy_thin_repo legacy-context
write_metadata legacy-context branch-protection <<'JSON'
{"required_status_checks":{"checks":[{"context":"Dagger","app_id":15368},{"context":"Secret scan / gitleaks","app_id":15368}]}}
JSON

copy_thin_repo codeql
write_metadata codeql codeql-default <<'JSON'
{"state":"configured","languages":["python"]}
JSON

copy_thin_repo cloudflare-app
write_metadata cloudflare-app check-runs <<'JSON'
{"check_runs":[{"name":"Cloudflare Pages","app":{"slug":"cloudflare-workers-and-pages"}}]}
JSON

copy_thin_repo dependabot
write_metadata dependabot check-runs <<'JSON'
{"check_runs":[{"name":"Dependency update","app":{"slug":"dependabot"}}]}
JSON

copy_thin_repo gitguardian
write_metadata gitguardian check-runs <<'JSON'
{"check_runs":[{"name":"GitGuardian Security Checks","app":{"slug":"gitguardian"}}]}
JSON
write_metadata gitguardian branch-protection <<'JSON'
{"required_status_checks":{"checks":[{"context":"Dagger","app_id":15368}]}}
JSON

write_workflow workflow-run-safe deploy.yml <<'YAML'
name: Deploy
on:
  workflow_run:
    workflows: [Dagger]
    types: [completed]
permissions: {contents: read}
jobs:
  dagger:
    if: >-
      github.event.workflow_run.event == 'push' &&
      github.event.workflow_run.conclusion == 'success' &&
      github.event.workflow_run.head_repository.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - uses: dagger/dagger-for-github@27b130bf0f79a7f6fbbbe0fbca6760dc9bb40a77
        with: {version: "0.21.8", verb: call, args: deploy}
YAML
cp -R "$fixtures/thin/.dagger" "$fixtures/workflow-run-safe/.dagger"
cp "$fixtures/thin/dagger.json" "$fixtures/workflow-run-safe/dagger.json"

cp -R "$fixtures/workflow-run-safe" "$fixtures/workflow-run-unsafe"
ruby -0pi -e 'sub(/    if: >-\n(?:      .*\n){3}/, "")' \
  "$fixtures/workflow-run-unsafe/.github/workflows/deploy.yml"

if [[ ! -f "$scanner" ]]; then
  report "Dagger control-plane scanner is missing"
else
  expect_exit 0 thin "a pinned, credential-safe Dagger ingress is accepted"
  expect_exit 1 inline "an inline repository gate is rejected"
  expect_exit 1 reusable "a reusable workflow job is rejected"
  expect_exit 1 helper "a helper job beside Dagger is rejected"
  expect_exit 1 extra-shell "an arbitrary shell step after Dagger is rejected"
  expect_exit 1 mutable "moving action and engine references are rejected"
  expect_exit 1 credentials "checkout credentials must not persist"
  expect_exit 1 unsafe-pr "pull requests cannot receive write authority or secrets"
  expect_exit 1 empty "a repository with no workflow cannot pass vacuously"
  expect_exit 1 no-module "a Dagger workflow without a Dagger module is rejected"
  expect_exit 1 implicit "implicit currentWorkspace access is rejected"
  expect_exit 1 string-secret "credential-shaped strings must use Dagger Secret"
  expect_exit 0 typescript "a typed Workspace constructor is a valid TypeScript source boundary"
  expect_exit 0 generated-sdk "generated Dagger SDK code is outside repository-authored policy"
  expect_exit 0 publisher "an enumerated source-free PyPI bridge is accepted"
  expect_exit 1 unsafe-publisher "a privileged bridge that checks out source is rejected"
  expect_exit 1 global-write "ordinary Dagger ingress cannot grant write permissions"
  expect_exit 1 array-pr "array-form pull request ingress cannot grant write permissions"
  expect_exit 1 no-jobs "a workflow with zero jobs cannot pass vacuously"
  expect_exit 0 metadata "an enumerated source-free GitHub metadata projection is accepted"
  expect_exit 0 protected "Dagger is the only allowed required execution context"
  expect_exit 0 protected-graphql "GraphQL branch-protection metadata enforces the same context contract"
  expect_exit 1 legacy-context "legacy required execution contexts are rejected"
  expect_exit 1 codeql "managed CodeQL execution is rejected"
  expect_exit 1 cloudflare-app "an independent deployment app check is rejected"
  expect_exit 0 dependabot "Dependabot update automation is not classified as an execution gate"
  expect_exit 0 gitguardian "GitGuardian may remain a non-required external observer"
  expect_scan_stderr gitguardian 'external-advisory.*gitguardian' \
    "GitGuardian is reported explicitly as an external advisory"
  expect_exit 0 workflow-run-safe "a repository-, push-, and success-pinned workflow_run is accepted"
  expect_exit 1 workflow-run-unsafe "an unguarded workflow_run Dagger job is rejected"

  mkdir -p "$fixtures/fleet"
  cp -R "$fixtures/thin" "$fixtures/fleet/thin"
  : > "$fixtures/empty-allowlist.txt"
  expect_command_exit 0 "the local fleet runner accepts a clean repository" \
    "$runner" --local "$fixtures/fleet" --consumers thin \
    --allowlist "$fixtures/empty-allowlist.txt" --today 2026-08-25
  expect_command_exit 2 "the fleet runner fails closed when a repository is absent" \
    "$runner" --local "$fixtures/fleet" --consumers missing \
    --allowlist "$fixtures/empty-allowlist.txt" --today 2026-08-25

  mkdir -p "$fixtures/remote/demo" "$fixtures/bin"
  cp -R "$fixtures/thin" "$fixtures/remote/demo/thin"
  cat > "$fixtures/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "repo clone" ]]; then
  cp -R "$FAKE_REMOTE_ROOT/${3#*/}" "$4"
  exit 0
fi
endpoint="$2"
if [[ "$endpoint" == "graphql" ]]; then
  printf '%s\n' '{"data":{"repository":{"branchProtectionRules":{"nodes":[{"pattern":"main","requiresStatusChecks":true,"requiredStatusCheckContexts":["Dagger"]}]}}}}'
  exit 0
fi
case "$endpoint" in
  */code-scanning/default-setup)
    printf '%s\n' '{"state":"not-configured"}'
    ;;
  */commits/main/check-runs*)
    printf '%s\n' 'github-actions'
    ;;
  */pulls*)
    printf '%s\n' 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    ;;
  */commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/check-runs*)
    printf '%s\n' 'gitguardian'
    ;;
  *) exit 2 ;;
esac
SH
  chmod +x "$fixtures/bin/gh"
  expect_command_exit 0 "the remote runner scans source plus hosted metadata" \
    env GH_BIN="$fixtures/bin/gh" FAKE_REMOTE_ROOT="$fixtures/remote/demo" \
    "$runner" --owner demo --consumers thin \
    --allowlist "$fixtures/empty-allowlist.txt" --today 2026-08-25

  baseline="$fixtures/allowlist.txt"
  fingerprint="$(first_fingerprint allowlisted)"
  printf 'allowlisted/ci.yml/gate|%s|2026-09-30|phase-0 migration\n' "$fingerprint" > "$baseline"
  expect_exit 0 allowlisted "an exact unexpired bootstrap entry is accepted" \
    --allowlist "$baseline" --today 2026-08-25
  expect_scan_output allowlisted $'\tALLOWLISTED$' \
    "approved migration debt is labeled in detector output" \
    --allowlist "$baseline" --today 2026-08-25
  printf '# a comment is not execution behavior\n' >> "$fixtures/allowlisted/.github/workflows/ci.yml"
  expect_exit 0 allowlisted "comments do not invalidate a semantic fingerprint" \
    --allowlist "$baseline" --today 2026-08-25
  expect_exit 1 allowlisted "an expired bootstrap entry fails closed" \
    --allowlist "$baseline" --today 2026-10-01
  ruby -pi -e 'gsub("uv run poe gate", "uv run poe gate --unsafe")' \
    "$fixtures/allowlisted/.github/workflows/ci.yml"
  expect_exit 1 allowlisted "a semantic job mutation invalidates its bootstrap entry" \
    --allowlist "$baseline" --today 2026-08-25
  expect_scan_output allowlisted $'\tNEW$' \
    "new execution drift is labeled in detector output" \
    --allowlist "$baseline" --today 2026-08-25
fi

if [[ "$failures" -ne 0 ]]; then
  printf '%d Dagger control-plane case(s) failed.\n' "$failures" >&2
  exit 1
fi

printf 'Dagger control-plane cases passed.\n'
