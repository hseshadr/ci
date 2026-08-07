# Report caller jobs that grant a reusable workflow LESS than it declares it needs.
#
# WHY THIS EXISTS: this is the one workflow defect that reports nothing at all.
#
# When a caller job's `permissions:` are narrower than the called workflow's own
# `permissions:`, GitHub refuses the run before any job starts:
#
#   requesting 'pull-requests: read', but is only allowed 'pull-requests: none'
#
# The run's conclusion is `startup_failure`, and — this is the part that matters —
# it emits ZERO check runs. Not one red check. Nothing. Measured on this
# repository: run 31127046921 (PR #18, ci.yml) has `jobs: 0`, and the check-runs
# API for its head SHA lists only the two checks from OTHER workflows. `Security
# policy` and `Secret scan (own brick) / gitleaks` were simply absent.
#
# Branch protection cannot tell that apart from "hasn't reported yet". A required
# check that CANNOT RUN looks exactly like a required check that is still queued,
# so the PR sits pending instead of going red, and the gate that was supposed to
# be un-skippable is skipped in silence. Same shape as the bug PR #18 exists to
# fix: a secret scan that scanned 0 commits and reported success.
#
# So the check has to be STATIC. A guard that waits for a run to tell it something
# is a guard that learns nothing from the failure mode it is written for.
#
# WHAT IS COMPARED
#   grant  — the caller job's `permissions:` if it declares one; otherwise the
#            caller workflow's top-level `permissions:`. A job-level block
#            REPLACES the top level, it does not merge with it — the trap that
#            produced this bug, since ci.yml's top level was already read-only and
#            the job-level block silently dropped `pull-requests` to `none`.
#   need   — the called workflow's own top-level `permissions:`.
#
# Anything the callee names above `none` must be granted at that level or higher
# (none < read < write). Scopes the grant does not list are `none`.
#
# WHAT IS NOT COMPARED
#   A third-party reusable workflow lives in a repository this scanner cannot
#   read, so it is neither blamed nor counted. `--count` reports only the pairs
#   that were actually resolved, so the caller of this scanner can refuse a run
#   that resolved nothing — a scanner that stopped resolving would otherwise be
#   indistinguishable from a clean tree.
#
# Output: one `<file>\t<reason>` line per violation; empty output means clean.
#
# Usage:
#   scan-caller-permissions.rb --ci-root DIR [--count] FILE...
require "yaml"

LEVELS = { "none" => 0, "read" => 1, "write" => 2 }.freeze

# An unrecognised level gets the loudest verdict, never the quietest: as a
# requirement it is treated as `write`, as a grant it is treated as `none`.
def required_level(value)
  LEVELS.fetch(value.to_s.strip, LEVELS["write"])
end

def granted_level(value)
  LEVELS.fetch(value.to_s.strip, LEVELS["none"])
end

# Normalise every spelling of a `permissions:` value to a scope => level hash.
# `nil` means the key was absent, which is NOT the same as `{}` (all none).
def normalise(value)
  case value
  when nil then nil
  when Hash then value
  when String
    case value.strip
    when "write-all" then :all_write
    when "read-all" then :all_read
    when "" then {}
    else :unknown
    end
  else :unknown
  end
end

# Highest level this grant confers on `scope`.
def grant_for(grant, scope)
  case grant
  when :all_write then LEVELS["write"]
  when :all_read then LEVELS["read"]
  when :unknown then LEVELS["none"]
  when Hash then granted_level(grant[scope])
  else LEVELS["none"]
  end
end

# Every scope the callee asks for above `none`, as scope => required level.
def demands(need)
  case need
  when :all_write then { "*" => LEVELS["write"] }
  when :all_read then { "*" => LEVELS["read"] }
  when :unknown then { "*" => LEVELS["write"] }
  when Hash
    need.each_with_object({}) do |(scope, level), acc|
      wanted = required_level(level)
      acc[scope] = wanted if wanted > LEVELS["none"]
    end
  else {}
  end
end

def level_name(level)
  LEVELS.key(level) || "write"
end

# Absolute path of the called workflow, or nil when it is not readable from here.
#
# `./x` names a file in the CALLER's own repository. That is this repository only
# when the caller itself lives here; an example is written for a consumer repo, so
# its `./` refs point at a tree this scanner has never seen.
def resolve_callee(uses, file, ci_root)
  ref = uses.to_s.strip
  local_prefix = File.join(ci_root, ".github", "workflows")

  if ref.start_with?("./")
    return nil unless File.expand_path(file).start_with?(local_prefix + File::SEPARATOR)

    return File.join(ci_root, ref.sub(%r{\A\./}, ""))
  end

  match = %r{\Ahseshadr/ci/(\.github/workflows/[^@]+)@}.match(ref)
  match && File.join(ci_root, match[1])
end

def load_document(path)
  document = YAML.safe_load(File.read(path), aliases: true)
  document.is_a?(Hash) ? document : nil
rescue StandardError
  nil
end

# Reasons this one caller job cannot start, plus whether the pair resolved.
def job_findings(job_id, job, top_level, file, ci_root)
  callee = resolve_callee(job["uses"], file, ci_root)
  return [false, []] if callee.nil?

  callee_document = load_document(callee)
  return [false, []] if callee_document.nil?

  need = normalise(callee_document["permissions"])
  return [true, []] if need.nil?

  # No declaration anywhere means the grant is the repository's default token
  # setting, which is not in this tree and not knowable statically. Refusing is
  # the only answer that cannot be wrong by accident.
  unless job.key?("permissions") || !top_level.nil?
    return [true, ["job `#{job_id}` calls #{File.basename(callee)} but declares no `permissions:` at " \
                  "job or workflow level — the grant is the repository default, which cannot be checked here"]]
  end

  grant = normalise(job.key?("permissions") ? job["permissions"] : top_level)
  source = job.key?("permissions") ? "job-level" : "workflow-level"

  reasons = demands(need).map do |scope, wanted|
    held = grant_for(grant, scope)
    next if held >= wanted

    "job `#{job_id}` grants `#{scope}: #{level_name(held)}` (#{source}) but #{File.basename(callee)} " \
      "requires `#{scope}: #{level_name(wanted)}` — the run would end in startup_failure with NO check runs"
  end.compact

  [true, reasons]
end

def scan(file, ci_root)
  document = load_document(file)
  return [0, []] if document.nil?

  jobs = document["jobs"]
  return [0, []] unless jobs.is_a?(Hash)

  resolved = 0
  reasons = jobs.flat_map do |job_id, job|
    next [] unless job.is_a?(Hash) && job.key?("uses")

    pair_resolved, pair_reasons = job_findings(job_id, job, document["permissions"], file, ci_root)
    resolved += 1 if pair_resolved
    pair_reasons
  end

  [resolved, reasons]
end

count_only = false
ci_root = nil
files = []
arguments = ARGV.dup
until arguments.empty?
  argument = arguments.shift
  case argument
  when "--count" then count_only = true
  when "--ci-root" then ci_root = arguments.shift
  else files << argument
  end
end

abort "scan-caller-permissions.rb: --ci-root is required" if ci_root.nil?
ci_root = File.expand_path(ci_root)

total_resolved = 0
files.each do |file|
  resolved, reasons = scan(file, ci_root)
  total_resolved += resolved
  reasons.each { |reason| puts "#{file}\t#{reason}" } unless count_only
end

puts total_resolved if count_only
