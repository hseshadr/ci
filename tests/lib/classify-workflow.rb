# frozen_string_literal: true

# Classify GitHub Actions workflows by the CONTROL each one implements, and say
# whether that control is adopted from hseshadr/ci or hand-rolled locally.
#
#   Usage:  ruby tests/lib/classify-workflow.rb <workflow.yml> ...
#   Output: one TAB-separated line per (file, control) pair; nothing at all for a
#           file that implements no control this repository publishes.
#
#       <path>\t<category>\t<verdict>\t<evidence>
#
#   verdict is one of:
#       ADOPTED             calls the hseshadr/ci reusable workflow or composite
#       ADOPTED-BY-PATTERN  inlined, but matches a documented ci-shipped pattern
#       DRIFT               hand-rolls a control hseshadr/ci already publishes
#
# Classification is by BEHAVIOUR — the parsed `uses:`, `run:` and `with:` values —
# not by filename. A repository that renames deploy.yml to release-site.yml has
# not stopped hand-rolling a Pages deploy. Filename is consulted only as a weak
# secondary signal, and only for pages-deploy, where a bespoke deploy may drive
# wrangler through a wrapper script this parser cannot see.
#
# Parse errors are skipped rather than raised: one malformed consumer workflow
# must not abort a sweep across seven repositories.
#
# Lives in tests/lib/ so tests/consumer-drift-cases.sh can drive it against
# synthetic fixtures in both polarities without touching the network.

require "yaml"

VERDICT_ADOPTED = "ADOPTED"
VERDICT_PATTERN = "ADOPTED-BY-PATTERN"
VERDICT_DRIFT = "DRIFT"

# Publish workflows legitimately re-run the repository's gate on the tagged ref
# before uploading — that is a step inside the publish pattern hseshadr/ci ships,
# not a second gate implementation. Suppress ONLY gate drift, ONLY inside a
# workflow that actually publishes, so the suppression cannot hide a hand-rolled
# gate in an ordinary ci.yml.
GATE_CATEGORIES = %w[python-gate frontend-gate].freeze
PUBLISH_CATEGORIES = %w[npm-publish pypi-publish].freeze

def walk(value, &block)
  yield value if value.is_a?(Hash)
  children = value.is_a?(Hash) ? value.values : value
  children.each { |child| walk(child, &block) } if children.is_a?(Array)
end

# One parsed workflow, with the handful of projections the rules need.
class Workflow
  attr_reader :path, :raw, :document

  def initialize(path)
    @path = path
    @raw = File.read(path)
    parsed = YAML.safe_load(@raw, aliases: true)
    @document = parsed.is_a?(Hash) ? parsed : {}
  end

  def uses
    @uses ||= strings_under(@document, "uses")
  end

  # `run:` scripts with shell comments removed.
  #
  # WHY: the detectors match command names against this text, and a COMMENT is not
  # a command. almamesh/deploy.yml's only occurrence of the word "gitleaks" is
  # `# (gitleaks false-positives on inline keys) ...` inside an unrelated "Ping
  # IndexNow" step, and matching raw text reported that file as hand-rolling a
  # secret scan. It was 1 of the 30 findings in the first live sweep, and the only
  # false one. A detector that cries wolf gets ignored, which costs more than the
  # finding was worth.
  def runs
    @runs ||= strings_under(@document, "run").map { |script| strip_shell_comments(script) }
  end

  # Every scalar under any `with:` map. cloudflare/wrangler-action carries the
  # whole deploy in `with: {command: pages deploy dist}`, where no `run:` block
  # ever appears — a run-only scanner is blind to the commonest hand-rolled form.
  def with_values
    @with_values ||= begin
      values = []
      walk(@document) do |node|
        with = node["with"]
        values.concat(with.values.map(&:to_s)) if with.is_a?(Hash)
      end
      values
    end
  end

  def basename
    File.basename(@path)
  end

  # The first step anywhere in the file whose `uses:` matches.
  def step_using(pattern)
    found = nil
    walk(@document) do |node|
      ref = node["uses"]
      found = node if found.nil? && ref.is_a?(String) && ref.match?(pattern)
    end
    found
  end

  # Jobs containing a step whose `uses:` matches. OIDC permissions are declared
  # on the JOB, so the enclosing job is what has to be inspected.
  def jobs_using(pattern)
    jobs = @document["jobs"]
    return [] unless jobs.is_a?(Hash)

    jobs.values.select do |job|
      job.is_a?(Hash) && strings_under(job, "uses").any? { |ref| ref.match?(pattern) }
    end
  end

  private

  # Cut each line at its first UNQUOTED '#' that begins a word — shell comment
  # rules. Quote tracking is what keeps this from over-stripping: a naive cut at
  # the first '#' would silently delete the real command in
  # `echo "release #42" && gitleaks detect`, turning a false positive into a false
  # negative, which is the worse of the two failures for a security detector.
  def strip_shell_comments(script)
    script.lines.map { |line| strip_line_comment(line) }.join
  end

  def strip_line_comment(line)
    quote = nil
    line.each_char.with_index do |char, index|
      if quote
        quote = nil if char == quote
      elsif ["'", '"'].include?(char)
        quote = char
      elsif char == "#" && (index.zero? || line[index - 1].match?(/\s/))
        return "#{line[0, index].rstrip}\n"
      end
    end
    line
  end

  def strings_under(root, key)
    found = []
    walk(root) do |node|
      value = node[key]
      found << value if value.is_a?(String)
    end
    found
  end
end

def any_match?(strings, pattern)
  strings.any? { |candidate| candidate.match?(pattern) }
end

# --- control rules ----------------------------------------------------------
# markers   substrings of a `uses:` ref that prove adoption
# detector  ->(workflow) evidence String when the file hand-rolls the control
# exemption ->(workflow) evidence String when an inlined form is nonetheless the
#           documented ci-shipped pattern (pypi-publish only, see below)

PAGES_ACTION = %r{cloudflare/wrangler-action}.freeze
PAGES_RUN = /wrangler[^\n]*pages\s+deploy/.freeze
PAGES_COMMAND = /(\A|\s)pages\s+deploy\b/.freeze

detect_pages_deploy = lambda do |wf|
  return "uses cloudflare/wrangler-action" if any_match?(wf.uses, PAGES_ACTION)
  return "runs `wrangler pages deploy`" if any_match?(wf.runs, PAGES_RUN)
  return "passes `pages deploy` to an action" if any_match?(wf.with_values, PAGES_COMMAND)

  named_deploy = wf.basename.start_with?("deploy")
  if named_deploy && wf.raw.match?(/CLOUDFLARE_/) && wf.raw.match?(/dist/)
    return "named deploy* and ships a dist dir with Cloudflare credentials"
  end

  nil
end

detect_secret_scan = lambda do |wf|
  return "uses gitleaks/gitleaks-action" if any_match?(wf.uses, %r{gitleaks/gitleaks-action})
  return "runs gitleaks" if any_match?(wf.runs, /\bgitleaks\b/)

  nil
end

# `uv run poe audit` is the dependency audit wearing a task-runner hat — every
# repository here routes pip-audit through poe. It is the dependency-audit
# control, not a Python gate, and classifying it as the latter would both
# under-report the audit and invent a gate nobody wrote.
POE_AUDIT = /\buv\s+run\s+poe\s+audit\b/.freeze
POE_TASK = /\buv\s+run\s+poe\b/.freeze

detect_dependency_audit = lambda do |wf|
  return "uses pypa/gh-action-pip-audit" if any_match?(wf.uses, %r{pypa/gh-action-pip-audit})
  return "runs pip-audit" if any_match?(wf.runs, /\bpip-audit\b/)
  return "runs `uv run poe audit`" if any_match?(wf.runs, POE_AUDIT)
  return "runs pnpm audit" if any_match?(wf.runs, /\bpnpm\s+audit\b/)
  return "runs npm audit" if any_match?(wf.runs, /\bnpm\s+audit\b/)

  nil
end

detect_python_gate = lambda do |wf|
  gate_runs = wf.runs.select { |script| script.match?(POE_TASK) && !script.match?(POE_AUDIT) }
  return "runs `uv run poe <task>`" unless gate_runs.empty?

  nil
end

detect_frontend_gate = lambda do |wf|
  return "runs `pnpm gate`" if any_match?(wf.runs, /\bpnpm(\s+-\S+)*\s+(run\s+)?gate\b/)
  return "installs Playwright browsers" if any_match?(wf.runs, /playwright\s+install/)

  nil
end

detect_npm_publish = lambda do |wf|
  return "runs `npm|pnpm publish`" if any_match?(wf.runs, /\b(npm|pnpm)\s+publish\b/)

  nil
end

PYPI_ACTION = %r{pypa/gh-action-pypi-publish}.freeze

detect_pypi_publish = lambda do |wf|
  return "uses pypa/gh-action-pypi-publish" if any_match?(wf.uses, PYPI_ACTION)

  nil
end

# PyPI Trusted Publishing matches the OIDC token's `job_workflow_ref`, and for a
# job that `uses:` a CROSS-REPO reusable workflow that ref names hseshadr/ci's
# file — never the consumer's own publish.yml, which is what the trusted
# publisher is registered against. Cross-repo reuse therefore ALWAYS fails with
# "invalid-publisher", so hseshadr/ci ships examples/*/publish.yml inline-PyPI
# examples instead of a callable workflow. An inline job that carries the two
# properties that make the shipped example safe — OIDC on the job and signed
# attestations on the upload — is adopting the published pattern, not drifting.
# Anything weaker (a password, attestations off, no id-token) is drift.
exempt_pypi_publish = lambda do |wf|
  jobs = wf.jobs_using(PYPI_ACTION)
  return nil if jobs.empty?

  oidc = jobs.any? do |job|
    permissions = job["permissions"]
    permissions.is_a?(Hash) && permissions["id-token"].to_s == "write"
  end
  step = wf.step_using(PYPI_ACTION)
  with = step.is_a?(Hash) && step["with"].is_a?(Hash) ? step["with"] : {}
  attested = with["attestations"].to_s == "true"
  return nil unless oidc && attested

  "inline PyPI Trusted Publishing with id-token: write + attestations: true"
end

Rule = Struct.new(:category, :provider, :markers, :detector, :exemption)

RULES = [
  Rule.new(
    "pages-deploy",
    "cloudflare-pages-deploy.yml + the pages-deploy-dist composite",
    [
      "hseshadr/ci/.github/workflows/cloudflare-pages-deploy.yml",
      "hseshadr/ci/.github/actions/pages-deploy-dist"
    ],
    detect_pages_deploy,
    nil
  ),
  Rule.new(
    "secret-scan",
    "secret-scan.yml",
    ["hseshadr/ci/.github/workflows/secret-scan.yml"],
    detect_secret_scan,
    nil
  ),
  Rule.new(
    "dependency-audit",
    "security-audit.yml",
    ["hseshadr/ci/.github/workflows/security-audit.yml"],
    detect_dependency_audit,
    nil
  ),
  Rule.new(
    "python-gate",
    "python-gate.yml",
    ["hseshadr/ci/.github/workflows/python-gate.yml"],
    detect_python_gate,
    nil
  ),
  # The setup-playwright composite counts as adoption here for the same reason
  # pages-deploy-dist does: for browser installs the COMPOSITE is the shared
  # unit. A frontend job that needs something frontend-gate.yml cannot express
  # (edge-reco's model-weights cache, for one) still adopts the published
  # browser-install step, and this repository's own examples/ ship exactly that
  # shape. A bare `playwright install` adopts nothing and stays drift.
  Rule.new(
    "frontend-gate",
    "frontend-gate.yml or the setup-playwright composite",
    [
      "hseshadr/ci/.github/workflows/frontend-gate.yml",
      "hseshadr/ci/.github/actions/setup-playwright"
    ],
    detect_frontend_gate,
    nil
  ),
  Rule.new(
    "npm-publish",
    "ts-publish.yml",
    ["hseshadr/ci/.github/workflows/ts-publish.yml"],
    detect_npm_publish,
    nil
  ),
  Rule.new(
    "pypi-publish",
    "the inline-PyPI examples/*/publish.yml pattern",
    ["hseshadr/ci/.github/workflows/python-publish.yml"],
    detect_pypi_publish,
    exempt_pypi_publish
  )
].freeze

def adoption_marker(workflow, rule)
  rule.markers.find do |marker|
    workflow.uses.any? { |ref| ref.include?(marker) }
  end
end

def classify_rule(workflow, rule)
  marker = adoption_marker(workflow, rule)
  evidence = rule.detector.call(workflow)
  return nil if marker.nil? && evidence.nil?
  return [rule.category, VERDICT_ADOPTED, "calls #{marker}"] if marker

  exempt = rule.exemption && rule.exemption.call(workflow)
  return [rule.category, VERDICT_PATTERN, exempt] if exempt

  [rule.category, VERDICT_DRIFT, "#{evidence}; hseshadr/ci publishes #{rule.provider}"]
end

# Drop gate DRIFT from a workflow that publishes — see GATE_CATEGORIES above.
# Adopted gate findings survive: calling python-gate.yml from a publish workflow
# is worth showing, and showing it cannot hide anything.
def suppress_publish_gate_drift(findings)
  publishes = findings.any? { |finding| PUBLISH_CATEGORIES.include?(finding[0]) }
  return findings unless publishes

  findings.reject do |category, verdict, _evidence|
    GATE_CATEGORIES.include?(category) && verdict == VERDICT_DRIFT
  end
end

def classify(path)
  workflow = Workflow.new(path)
  findings = RULES.map { |rule| classify_rule(workflow, rule) }.compact
  suppress_publish_gate_drift(findings)
end

ARGV.each do |path|
  begin
    findings = classify(path)
  rescue StandardError
    next
  end

  findings.each do |category, verdict, evidence|
    puts([path, category, verdict, evidence].join("\t"))
  end
end
