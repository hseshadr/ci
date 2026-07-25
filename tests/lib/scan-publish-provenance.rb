# Report workflows that ship a release WITHOUT build provenance.
#
# WHY THIS EXISTS: provenance is the one property of a publish pipeline that
# fails SILENTLY. Drop `--provenance` from an npm publish or `attestations:`
# from a PyPI upload and the release still goes out, the run is still green,
# and the only way to notice is to ask the registry months later. Every other
# publish defect announces itself; this one just quietly stops signing.
#
# Three properties are checked per job, and each is a real credential fact:
#
#   1. npm      — an inline `npm publish` must pass `--provenance`, and a caller
#                 of the shared ts-publish.yml must not set `provenance: false`.
#   2. PyPI     — a `pypa/gh-action-pypi-publish` step must set
#                 `attestations: true` (PEP 740 / SLSA). Absent is a violation,
#                 not a default: the action's own default has moved before.
#   3. OIDC     — a publishing job must grant `id-token: write`. That token IS
#                 the credential under Trusted Publishing; without it there is
#                 nothing to sign the provenance with, and a release that loses
#                 the permission silently degrades to "cannot authenticate".
#
# A reusable workflow's own `workflow_call` input defaults count as part of the
# contract: a `provenance`/`attestations` input that DEFAULTS to false hands
# every forgetful caller an unsigned release, so that default is checked too.
#
# THIS IS A YAML PARSE, NOT A GREP, on purpose. The check it replaces would have
# been satisfied by the string `attestations: true` sitting in a comment above a
# step that never set it — the exact shape-not-property failure this repo's
# other guards were rewritten to avoid.
#
# Output: one `<file>\t<reason>` line per violation; empty output means clean.
require "yaml"

TS_PUBLISH = "ts-publish.yml".freeze
PYPI_ACTION = "pypa/gh-action-pypi-publish@".freeze

# The `on:` key parses to the YAML 1.1 boolean `true`, not the string "on".
def triggers(document)
  value = document["on"]
  value = document[true] if value.nil?
  value.is_a?(Hash) ? value : {}
end

def call_inputs(document)
  inputs = triggers(document).dig("workflow_call", "inputs")
  inputs.is_a?(Hash) ? inputs : {}
end

# True when `value` means "on". Resolves a `${{ inputs.x }}` expression against
# this workflow's own `workflow_call` input default, so a reusable workflow that
# forwards an input is judged by the default its callers actually inherit.
def enabled?(value, document, seen = [])
  case value
  when true then true
  when nil, false then false
  when String then enabled_string?(value, document, seen)
  else false
  end
end

def enabled_string?(text, document, seen)
  match = /\A\$\{\{\s*inputs\.([A-Za-z0-9_-]+)\s*\}\}\z/.match(text.strip)
  return text.strip.casecmp?("true") if match.nil?

  name = match[1]
  return false if seen.include?(name)

  input = call_inputs(document)[name]
  return false unless input.is_a?(Hash)

  enabled?(input["default"], document, seen + [name])
end

def grants_oidc?(permissions)
  case permissions
  when String then permissions.strip == "write-all"
  when Hash then permissions["id-token"].to_s.strip == "write"
  else false
  end
end

def steps_of(job)
  steps = job["steps"]
  steps.is_a?(Array) ? steps.select { |step| step.is_a?(Hash) } : []
end

# Violations raised by ONE step, plus whether that step publishes at all.
def step_findings(step, document)
  uses = step["uses"].to_s
  run = step["run"].to_s
  with = step["with"].is_a?(Hash) ? step["with"] : {}
  reasons = []
  publishes = false

  if uses.start_with?(PYPI_ACTION)
    publishes = true
    unless enabled?(with["attestations"], document)
      reasons << "PyPI publish step does not set `attestations: true` (PEP 740 provenance)"
    end
  end

  if /\bnpm\s+publish\b/.match?(run)
    publishes = true
    reasons << "inline `npm publish` never passes --provenance" unless run.include?("--provenance")
  end

  [publishes, reasons]
end

# Violations raised by a job that CALLS the shared npm publish workflow.
def caller_findings(job)
  return [false, []] unless job["uses"].to_s.include?(TS_PUBLISH)

  with = job["with"].is_a?(Hash) ? job["with"] : {}
  return [true, []] unless with.key?("provenance")
  return [true, []] if with["provenance"] == true || with["provenance"].to_s.strip.casecmp?("true")

  [true, ["npm publish caller sets `provenance: false` — the release ships unsigned"]]
end

def job_findings(job, document)
  publishes, reasons = caller_findings(job)
  steps_of(job).each do |step|
    step_publishes, step_reasons = step_findings(step, document)
    publishes ||= step_publishes
    reasons.concat(step_reasons)
  end
  return [] unless publishes

  unless grants_oidc?(job["permissions"])
    reasons << "publishing job does not grant `id-token: write` — OIDC is the credential"
  end
  reasons
end

# A reusable workflow whose provenance switch DEFAULTS to off is a trap for
# every caller that does not know to override it.
def input_default_findings(document)
  call_inputs(document).map do |name, spec|
    next unless %w[provenance attestations].include?(name)
    next unless spec.is_a?(Hash)
    next if enabled?(spec["default"], document, [name])

    "reusable `#{name}` input defaults to off — callers inherit unsigned releases"
  end.compact
end

def findings(document)
  jobs = document["jobs"]
  reasons = jobs.is_a?(Hash) ? jobs.each_value.flat_map { |job| job.is_a?(Hash) ? job_findings(job, document) : [] } : []
  reasons + input_default_findings(document)
end

ARGV.each do |file|
  begin
    document = YAML.safe_load(File.read(file), aliases: true)
  rescue StandardError
    next
  end
  next unless document.is_a?(Hash)

  findings(document).each { |reason| puts "#{file}\t#{reason}" }
end
