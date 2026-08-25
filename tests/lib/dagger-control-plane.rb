# frozen_string_literal: true

# Enforce the fleet contract that GitHub only provides event ingress and Dagger
# owns every repository-authored execution job.

require "json"
require "open3"
require "optparse"
require "date"
require "digest"
require "yaml"

DAGGER_ACTION = %r{\Adagger/dagger-for-github@[0-9a-f]{40}\z}.freeze
CHECKOUT_ACTION = %r{\Aactions/checkout@[0-9a-f]{40}\z}.freeze
DOWNLOAD_ACTION = %r{\Aactions/download-artifact@[0-9a-f]{40}\z}.freeze
PYPI_ACTION = %r{\Apypa/gh-action-pypi-publish@[0-9a-f]{40}\z}.freeze
GITHUB_SCRIPT_ACTION = %r{\Aactions/github-script@[0-9a-f]{40}\z}.freeze
ENGINE_VERSION = /\A\d+\.\d+\.\d+\z/.freeze
# CodeQL execution is classified from its authoritative default-setup setting;
# its check app is output ownership, not a second independent control.
NON_EXECUTION_APPS = %w[dependabot github-actions github-advanced-security].freeze
ADVISORY_APPS = %w[gitguardian].freeze

Violation = Struct.new(:repo, :workflow, :job, :fingerprint, :reason) do
  def key
    [repo, workflow, job].join("/")
  end

  def to_tsv
    [repo, workflow, job, fingerprint, reason].join("\t")
  end
end

Bootstrap = Struct.new(:key, :fingerprint, :expires, :reason)

def canonical(value)
  return value.keys.sort.each_with_object({}) { |key, result| result[key] = canonical(value[key]) } if value.is_a?(Hash)
  return value.map { |child| canonical(child) } if value.is_a?(Array)

  value
end

def fingerprint(value)
  Digest::SHA256.hexdigest(JSON.generate(canonical(value)))
end

def walk(value, &block)
  yield value
  value.each_value { |child| walk(child, &block) } if value.is_a?(Hash)
  value.each { |child| walk(child, &block) } if value.is_a?(Array)
end

def contains_secret?(value)
  found = false
  walk(value) do |item|
    found = true if item.is_a?(String) && item.match?(/\bsecrets(?:\.|\[|\b)/)
  end
  found
end

def write_permissions?(permissions)
  return permissions.include?("write") if permissions.is_a?(String)
  return false unless permissions.is_a?(Hash)

  permissions.values.any? { |value| value.to_s == "write" }
end

def event_workflow?(document, event)
  triggers = document["on"] || document[true]
  return triggers.key?(event) if triggers.is_a?(Hash)
  return triggers.include?(event) if triggers.is_a?(Array)

  triggers.to_s == event
end

def thin_dagger_job_reasons(job)
  steps = job["steps"]
  return ["job must contain exactly checkout then Dagger"] unless steps.is_a?(Array) && steps.length == 2

  checkout, invocation = steps
  reasons = []
  reasons << "first step is not immutable checkout" unless checkout.is_a?(Hash) && checkout["uses"].to_s.match?(CHECKOUT_ACTION)
  persisted = checkout.is_a?(Hash) && checkout.fetch("with", {})["persist-credentials"]
  reasons << "checkout must set persist-credentials: false" unless persisted == false
  reasons << "second step is not immutable Dagger" unless invocation.is_a?(Hash) && invocation["uses"].to_s.match?(DAGGER_ACTION)
  version = invocation.is_a?(Hash) ? invocation.fetch("with", {})["version"].to_s : ""
  reasons << "Dagger engine version is not immutable" unless version.match?(ENGINE_VERSION)
  reasons
end

def pull_request_reasons(document, job)
  return [] unless event_workflow?(document, "pull_request")

  contains_secret?(job) ? ["pull request job references a secret"] : []
end

def write_permission_reasons(document, job)
  reasons = []
  reasons << "workflow grants write permission outside an enumerated bridge" if write_permissions?(document["permissions"])
  reasons << "job grants write permission outside an enumerated bridge" if write_permissions?(job["permissions"])
  reasons
end

def dagger_job_names(jobs)
  jobs.each_with_object([]) do |(name, job), names|
    next unless job.is_a?(Hash)

    steps = job["steps"]
    names << name if steps.is_a?(Array) && steps.any? { |step| step["uses"].to_s.match?(DAGGER_ACTION) }
  end
end

def needs_dagger?(job, names)
  needs = job["needs"]
  Array(needs).any? { |name| names.include?(name) }
end

def pypi_bridge_reasons(job, dagger_names)
  steps = job["steps"]
  uses = steps.is_a?(Array) ? steps.map { |step| step["uses"].to_s } : []
  reasons = []
  reasons << "publisher bridge does not need Dagger" unless needs_dagger?(job, dagger_names)
  reasons << "publisher bridge has no protected environment" if job["environment"].to_s.empty?
  reasons << "publisher bridge lacks id-token: write" unless job.fetch("permissions", {})["id-token"] == "write"
  expected = uses.length == 2 && uses[0].match?(DOWNLOAD_ACTION) && uses[1].match?(PYPI_ACTION)
  reasons << "publisher bridge is not source-free" unless expected
  reasons
end

def github_metadata_reasons(job, dagger_names)
  steps = job["steps"]
  uses = steps.is_a?(Array) ? steps.map { |step| step["uses"].to_s } : []
  permissions = job.fetch("permissions", {})
  reasons = []
  reasons << "metadata projection does not need Dagger" unless needs_dagger?(job, dagger_names)
  reasons << "metadata projection lacks checks: write" unless permissions["checks"] == "write"
  reasons << "metadata projection is not source-free" unless uses.length == 1 && uses[0].match?(GITHUB_SCRIPT_ACTION)
  reasons << "metadata projection references a secret" if contains_secret?(job)
  reasons
end

def bridge_reasons(entry, job, dagger_names)
  return pypi_bridge_reasons(job, dagger_names) if entry["kind"] == "pypi-publisher"
  return github_metadata_reasons(job, dagger_names) if entry["kind"] == "github-metadata"

  ["unknown bridge kind"]
end

def workflow_run_reasons(path)
  checker = File.join(__dir__, "workflow-run-pin.rb")
  output, error, status = Open3.capture3("ruby", checker, path)
  return output.lines.map(&:strip).reject(&:empty?) if status.exitstatus == 2
  return [] if [0, 1].include?(status.exitstatus)

  ["workflow_run policy failed: #{error.strip}"]
end

def workflow_run?(document)
  triggers = document["on"] || document[true]
  triggers.is_a?(Hash) && triggers.key?("workflow_run")
end

def workflow_violations(repo, path, bridges)
  document = YAML.safe_load(File.read(path), aliases: true)
  jobs = document.is_a?(Hash) ? document["jobs"] : nil
  unless jobs.is_a?(Hash) && !jobs.empty?
    violation = Violation.new(repo, File.basename(path), "<none>", fingerprint(document), "workflow has zero jobs")
    return [violation]
  end

  dagger_names = dagger_job_names(jobs)
  run_reasons = workflow_run?(document) ? workflow_run_reasons(path) : []
  jobs.map do |job_name, job|
    entry = bridges.find { |bridge| bridge["workflow"] == File.basename(path) && bridge["job"] == job_name }
    reasons = if !job.is_a?(Hash)
                ["job is not a map"]
              elsif entry
                bridge_reasons(entry, job, dagger_names)
              else
                thin_dagger_job_reasons(job)
              end
    reasons.concat(run_reasons.select { |reason| reason.include?("`#{job_name}`") })
    if job.is_a?(Hash) && entry.nil?
      reasons.concat(write_permission_reasons(document, job))
      reasons.concat(pull_request_reasons(document, job))
    end
    next if reasons.empty?

    behavior = {"on" => document["on"] || document[true], "permissions" => document["permissions"], "job" => job}
    Violation.new(repo, File.basename(path), job_name, fingerprint(behavior), reasons.join("; "))
  end.compact
end

def load_bridges(root)
  path = File.join(root, ".github/dagger-control-plane.yml")
  return [] unless File.file?(path)

  document = YAML.safe_load(File.read(path), aliases: true)
  bridges = document.is_a?(Hash) ? document["bridges"] : nil
  bridges.is_a?(Array) ? bridges : []
end

def module_violation(repo, digest, reason)
  Violation.new(repo, "dagger.json", "<module>", digest, reason)
end

def python_module_reasons(paths)
  checker = File.join(__dir__, "dagger_source_policy.py")
  output, error, status = Open3.capture3("python3", checker, *paths)
  return output.lines.map(&:strip).reject(&:empty?) if status.exitstatus == 1
  return [] if status.success?

  ["module policy failed: #{error.strip}"]
end

def typescript_module_reasons(paths)
  source = paths.map { |path| File.read(path) }.join("\n")
  reasons = []
  reasons << "module reads the implicit current workspace" if source.match?(/\bcurrent_?Workspace\s*\(/i)
  typed_source = source.match?(/\bsource\s*:\s*Directory\b/) && source.match?(/constructor\s*\([^)]*:\s*Workspace\b/m)
  reasons << "module has no typed Workspace/Directory source boundary" unless typed_source
  sensitive_string = source.match?(/\b(?:token|secret|password|credential|privateKey|apiKey)\??\s*:\s*string\b/i)
  reasons << "credential argument is typed string instead of Secret" if sensitive_string
  reasons
end

def authored_module_sources(source_root, extension)
  generated = %w[sdk node_modules .venv].map { |name| File.join(source_root, name) + File::SEPARATOR }
  Dir.glob(File.join(source_root, "**/*.#{extension}")).reject do |path|
    generated.any? { |prefix| path.start_with?(prefix) }
  end
end

def module_violations(repo, root)
  config_path = File.join(root, "dagger.json")
  return [module_violation(repo, fingerprint("missing"), "repository has no dagger.json")] unless File.file?(config_path)

  config = JSON.parse(File.read(config_path))
  reasons = []
  reasons << "Dagger engineVersion is not immutable" unless config["engineVersion"].to_s.match?(/\Av\d+\.\d+\.\d+\z/)
  source_root = File.join(root, config.fetch("source", "."))
  python_sources = authored_module_sources(source_root, "py")
  typescript_sources = authored_module_sources(source_root, "ts")
  reasons << "Dagger module has no source" if python_sources.empty? && typescript_sources.empty?
  reasons.concat(python_module_reasons(python_sources)) unless python_sources.empty?
  reasons.concat(typescript_module_reasons(typescript_sources)) unless typescript_sources.empty?
  return [] if reasons.empty?

  sources = (python_sources + typescript_sources).sort.map { |path| [path.delete_prefix(root), File.read(path)] }
  [module_violation(repo, fingerprint([config, sources]), reasons.join("; "))]
rescue JSON::ParserError => error
  [module_violation(repo, fingerprint(File.read(config_path)), "invalid dagger.json: #{error.message}")]
end

def metadata_document(root, name)
  path = File.join(root, ".control-plane/#{name}.json")
  File.file?(path) ? JSON.parse(File.read(path)) : nil
end

def metadata_violation(repo, job, value, reason)
  Violation.new(repo, "<metadata>", job, fingerprint(value), reason)
end

def protection_checks(document)
  rest = document.dig("required_status_checks", "checks")
  return rest if rest.is_a?(Array)

  rules = document.dig("data", "repository", "branchProtectionRules", "nodes")
  matching = Array(rules).select { |rule| File.fnmatch?(rule["pattern"].to_s, "main") }
  matching.flat_map do |rule|
    Array(rule["requiredStatusCheckContexts"]).map { |context| {"context" => context} }
  end
end

def protection_violations(repo, document)
  return [] unless document.is_a?(Hash)

  checks = protection_checks(document)
  contexts = checks.map { |check| check["context"].to_s }
  return [] if contexts == ["Dagger"]

  [metadata_violation(repo, "branch-protection", checks, "required contexts must be exactly Dagger")]
end

def codeql_violations(repo, document)
  return [] unless document.is_a?(Hash) && document["state"] == "configured"

  [metadata_violation(repo, "codeql-default", document, "managed CodeQL runs outside Dagger")]
end

def check_app_slugs(root)
  document = metadata_document(root, "check-runs")
  json_apps = document.is_a?(Hash) ? Array(document["check_runs"]).map { |run| run.dig("app", "slug").to_s } : []
  path = File.join(root, ".control-plane/check-apps.txt")
  text_apps = File.file?(path) ? File.readlines(path, chomp: true) : []
  (json_apps + text_apps).map(&:strip).reject(&:empty?).uniq
end

def check_app_violations(repo, apps)
  external = apps.reject do |slug|
    slug.empty? || NON_EXECUTION_APPS.include?(slug) || ADVISORY_APPS.include?(slug)
  end
  return [] if external.empty?

  [metadata_violation(repo, "check-apps", external, "independent check apps: #{external.join(', ')}")]
end

def advisory_apps(root)
  check_app_slugs(root) & ADVISORY_APPS
end

def metadata_violations(repo, root)
  protection_violations(repo, metadata_document(root, "branch-protection")) +
    codeql_violations(repo, metadata_document(root, "codeql-default")) +
    check_app_violations(repo, check_app_slugs(root))
end

def load_bootstraps(path)
  return [] if path.to_s.empty?

  File.readlines(path, chomp: true).map do |line|
    stripped = line.strip
    next if stripped.empty? || stripped.start_with?("#")

    key, digest, expires, reason = stripped.split("|", 4)
    abort "malformed bootstrap entry: #{line}" if [key, digest, expires, reason].any? { |value| value.to_s.empty? }
    Bootstrap.new(key, digest, Date.iso8601(expires), reason)
  end.compact
end

def approved?(violation, bootstraps, today)
  bootstraps.any? do |entry|
    entry.key == violation.key && entry.fingerprint == violation.fingerprint && entry.expires >= today
  end
end

def unapproved(violations, bootstraps, today)
  violations.reject { |violation| approved?(violation, bootstraps, today) }
end

options = {}
OptionParser.new do |parser|
  parser.on("--repo NAME") { |value| options[:repo] = value }
  parser.on("--path PATH") { |value| options[:path] = value }
  parser.on("--allowlist PATH") { |value| options[:allowlist] = value }
  parser.on("--today DATE") { |value| options[:today] = value }
end.parse!

abort "--repo is required" if options[:repo].to_s.empty?
abort "--path is required" if options[:path].to_s.empty?

workflows = Dir.glob(File.join(options[:path], ".github/workflows/*.{yml,yaml}"))
bridges = load_bridges(options[:path])
violations = if workflows.empty?
               [Violation.new(options[:repo], "<none>", "<none>", fingerprint("no-workflow"), "repository has no workflow")]
             else
               workflows.flat_map { |path| workflow_violations(options[:repo], path, bridges) }
             end
violations.concat(module_violations(options[:repo], options[:path]))
violations.concat(metadata_violations(options[:repo], options[:path]))
today = Date.iso8601(options.fetch(:today, Date.today.iso8601))
bootstraps = load_bootstraps(options[:allowlist])
new_violations = unapproved(violations, bootstraps, today)
advisory_apps(options[:path]).each { |app| warn "#{options[:repo]}: external-advisory #{app}" }
violations.each do |violation|
  verdict = approved?(violation, bootstraps, today) ? "ALLOWLISTED" : "NEW"
  puts [violation.to_tsv, verdict].join("\t")
end
warn "#{options[:repo]}: #{violations.length - new_violations.length} allowlisted, #{new_violations.length} new"
exit 1 unless new_violations.empty?
