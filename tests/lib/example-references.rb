# frozen_string_literal: true

# Resolve every reference an example makes against the repository it is written for.
#
#   Usage:  ruby tests/lib/example-references.rb --consumer-root <dir> <example.yml> ...
#   Output: one TAB-separated line per reference
#
#       <example>\t<status>\t<kind>\t<reference>\t<detail>
#
#   status is one of:
#       OK            the reference resolves in the consumer repository
#       MISSING       the reference does NOT resolve — the example is broken as drafted
#       UNVERIFIABLE  this checker cannot decide (no clone, no comparable ref, an
#                     unsupported command shape). NEVER counted as a pass.
#
# WHY THIS EXISTS
#   tests/consumer-drift.sh proves a CONSUMER diverges from what this repository
#   publishes. Nothing proved the mirror: that an example in examples/ still
#   converges to the consumer it names. tests/lint-examples.sh runs actionlint and
#   zizmor, which check YAML shape and workflow security — neither one resolves a
#   repo-relative path inside somebody else's repository, so both stayed green on
#   examples/edge-reco/ci.yml naming `frontend/.node-version`, a file edge-reco has
#   never had (it uses `.nvmrc`). `actions/setup-node` hard-fails on a missing
#   version file, so that example was RED as drafted and the gate passed it.
#   Every convergence PR in the portfolio begins "copy the example", so an
#   unchecked example is an unchecked migration.
#
# WHY IT READS A GIT REF, NOT THE WORKING TREE
#   A guard that reads a local working tree reports whatever the developer happens
#   to have checked out — uncommitted edits, a half-finished branch, a stash. That
#   produces false alarms, and a check that cries wolf gets ignored, which is barely
#   better than no check. Everything below is resolved against a COMMITTED ref
#   (origin/main by preference), and the ref plus its commit date are printed so a
#   stale clone discloses itself instead of silently deciding the answer.
#
# NO SILENT PASSES
#   Anything this program cannot decide is UNVERIFIABLE, never OK. The caller
#   (tests/example-fidelity.sh) treats UNVERIFIABLE as "not checked" and applies a
#   coverage floor, so a checker that stopped resolving anything cannot look clean.

require "yaml"
require "json"
require "open3"

STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
STATUS_UNVERIFIABLE = "UNVERIFIABLE"

# examples/<dir> names the repository the example is written for. One entry is not
# an identity: shared-libs-python was renamed to edgeproc-core on GitHub, and the
# local clone still carries the old directory name.
DIRECTORY_ALIASES = {
  "shared-libs-python" => %w[shared-libs-python edgeproc-core],
  "edgeproc-core" => %w[edgeproc-core shared-libs-python]
}.freeze

# Consumer-repo paths that arrive as `with:` inputs. Everything here is resolved
# relative to the consumer's repository root, because that is the directory a
# reusable workflow and a composite both start in.
FILE_INPUTS = %w[node-version-file package-json-file cache-dependency-path].freeze
DIRECTORY_INPUTS = %w[
  working-directory python-working-directory frontend-working-directory
  install-working-directory playwright-working-directory
].freeze

# Deliberately NOT existence-checked, with the reason, so a future reader does not
# "fix" the omission:
#   dist-dir    a build output; it does not exist until the build step runs.
#   cache-path  a download target; restore-model-cache creates it on a cache miss.
#               Its PARENT is checked instead — that is the part the repo owns.
SKIPPED_INPUTS = %w[dist-dir].freeze

# Which `with:` key names the directory a caller-supplied command runs from, per
# brick. Without this the command's script path is resolved against the wrong base.
COMMAND_BASES = {
  "restore-model-cache" => { "fetch-command" => "working-directory" },
  "cloudflare-pages-deploy.yml" => { "build-command" => "install-working-directory" }
}.freeze

def walk(value, &block)
  yield value if value.is_a?(Hash)
  children = value.is_a?(Hash) ? value.values : value
  children.each { |child| walk(child, &block) } if children.is_a?(Array)
end

# A consumer repository, read through a committed git ref.
class ConsumerRepo
  attr_reader :name, :path, :ref, :note

  def initialize(name, path)
    @name = name
    @path = path
    @cache = {}
    @ref, @note = resolve_ref
  end

  def available?
    !@ref.nil?
  end

  def describe
    return "no clone under the consumer root" unless @path
    return "clone at #{@path} has no comparable ref" unless @ref

    "#{@path} @ #{@ref} (#{@note})"
  end

  def file?(relative)
    object_type(relative) == "blob"
  end

  def directory?(relative)
    return true if relative.nil? || relative.empty? || relative == "."

    object_type(relative) == "tree"
  end

  def read(relative)
    out, _err, status = git("show", "#{@ref}:#{relative}")
    status.success? ? out : nil
  end

  # Every package.json committed on the resolved ref, so a `pnpm --filter <pkg>`
  # can be answered by package NAME instead of guessed at from a directory.
  def package_manifests
    @package_manifests ||= begin
      out, _err, status = git("ls-tree", "-r", "--name-only", @ref)
      status.success? ? out.lines.map(&:strip).select { |p| p.end_with?("package.json") } : []
    end
  end

  private

  def git(*args)
    Open3.capture3("git", "-C", @path, *args)
  end

  # origin/main first: it is what the consumer's default branch actually contains,
  # independent of whatever the local checkout is doing. The fallbacks exist so a
  # clone without a remote still yields an answer, and the chosen ref is reported.
  def resolve_ref
    return [nil, nil] unless @path && Dir.exist?(File.join(@path, ".git"))

    %w[origin/main origin/HEAD main HEAD].each do |candidate|
      out, _err, status = git("rev-parse", "--verify", "--quiet", "#{candidate}^{commit}")
      next unless status.success? && !out.strip.empty?

      date, = git("log", "-1", "--format=%cs", candidate)
      return [candidate, "committed #{date.strip}"]
    end
    [nil, nil]
  end

  def object_type(relative)
    @cache[relative] ||= begin
      out, _err, status = git("cat-file", "-t", "#{@ref}:#{relative}")
      status.success? ? out.strip : "absent"
    end
  end
end

# One example workflow, and the references it makes.
class Example
  attr_reader :path, :consumer_name

  def initialize(path, ci_root)
    @path = path
    @ci_root = ci_root
    @consumer_name = File.basename(File.dirname(path))
    @document = YAML.safe_load(File.read(path), aliases: true) || {}
    @findings = []
  end

  def references(repo)
    @findings = []
    walk(@document) { |node| inspect_node(node, repo) }
    @findings
  end

  private

  def record(status, kind, reference, detail)
    @findings << [@path, status, kind, reference, detail]
  end

  def inspect_node(node, repo)
    check_brick(node) if node["uses"].is_a?(String)
    check_with_inputs(node, repo)
    check_step_working_directory(node, repo)
    check_run(node, repo)
  end

  # ---- references into THIS repository ------------------------------------
  # An example naming a brick that has been renamed or deleted is broken in a way
  # no consumer-side check can see, so it is resolved here rather than there.
  def check_brick(node)
    ref = node["uses"]
    match = ref.match(%r{\Ahseshadr/ci/(\.github/(?:workflows/[^@]+|actions/[^@]+))@})
    return unless match

    target = match[1]
    manifest = target.end_with?(".yml") ? target : File.join(target, "action.yml")
    absolute = File.join(@ci_root, manifest)
    unless File.file?(absolute)
      record(STATUS_MISSING, "brick", target, "hseshadr/ci does not publish #{manifest}")
      return
    end
    record(STATUS_OK, "brick", target, "published at #{manifest}")
    check_brick_inputs(node, absolute, target)
  end

  # An example that passes an input the brick does not declare is silently
  # ignored by Actions at run time — the caller believes a control is configured
  # when nothing reads it. actionlint cannot see this: the brick lives in another
  # repository at a pinned SHA.
  def check_brick_inputs(node, manifest_path, target)
    with = node["with"]
    return unless with.is_a?(Hash)

    declared = declared_inputs(manifest_path)
    return record(STATUS_UNVERIFIABLE, "brick-inputs", target, "could not read declared inputs") if declared.nil?

    with.each_key do |key|
      if declared.include?(key)
        record(STATUS_OK, "brick-input", "#{target}:#{key}", "declared")
      else
        record(STATUS_MISSING, "brick-input", "#{target}:#{key}",
               "#{target} declares no input '#{key}' — Actions ignores it silently")
      end
    end
  end

  def declared_inputs(manifest_path)
    document = YAML.safe_load(File.read(manifest_path), aliases: true)
    return nil unless document.is_a?(Hash)

    return document["inputs"].keys if document["inputs"].is_a?(Hash)

    trigger = document["on"] || document[true]
    call = trigger.is_a?(Hash) ? trigger["workflow_call"] : nil
    return [] unless call.is_a?(Hash) && call["inputs"].is_a?(Hash)

    call["inputs"].keys
  rescue StandardError
    nil
  end

  # ---- references into the CONSUMER repository ----------------------------
  def check_with_inputs(node, repo)
    with = node["with"]
    return unless with.is_a?(Hash)

    with.each do |key, value|
      next if SKIPPED_INPUTS.include?(key)

      text = value.to_s
      next if text.empty? || text.include?("${{")

      check_input(key, text, node, repo)
    end
  end

  def check_input(key, text, node, repo)
    if FILE_INPUTS.include?(key)
      verify(repo, "file", text) { repo.file?(text) }
    elsif DIRECTORY_INPUTS.include?(key)
      verify(repo, "dir", text) { repo.directory?(text) }
    elsif key == "cache-path"
      parent = File.dirname(text)
      verify(repo, "dir", parent) { repo.directory?(parent) }
    elsif command_input?(node, key)
      check_command(text, command_base(node, key), repo)
    end
  end

  def brick_key(node)
    ref = node["uses"].to_s
    return nil unless ref.start_with?("hseshadr/ci/")

    ref.split("@").first.split("/").last
  end

  def command_input?(node, key)
    bases = COMMAND_BASES[brick_key(node)]
    !bases.nil? && bases.key?(key)
  end

  def command_base(node, key)
    input = COMMAND_BASES.fetch(brick_key(node)).fetch(key)
    with = node["with"]
    base = with.is_a?(Hash) ? with[input].to_s : ""
    base.empty? ? "." : base
  end

  def check_step_working_directory(node, repo)
    directory = node["working-directory"]
    return unless directory.is_a?(String) && !directory.include?("${{")

    verify(repo, "dir", directory) { repo.directory?(directory) }
  end

  def check_run(node, repo)
    script = node["run"]
    return unless script.is_a?(String)

    base = node["working-directory"]
    base = "." unless base.is_a?(String) && !base.include?("${{")
    check_command(script, base, repo)
  end

  # ---- command shapes -----------------------------------------------------
  def check_command(script, base, repo)
    check_package_scripts(script, base, repo)
    check_node_scripts(script, base, repo)
    check_poe_tasks(script, base, repo)
  end

  # `pnpm run <name>`, `pnpm -C <dir> run <name>`, `pnpm -F <pkg> run <name>`. A
  # script the consumer's package.json does not define fails the job with
  # ERR_PNPM_NO_SCRIPT.
  #
  # `-F` is pnpm's short form of `--filter`, and missing that costs a FALSE ALARM,
  # not a miss: edge-reco's `pnpm -C frontend -F frontend run build:pages` defines
  # build:pages in the workspace package named `frontend`
  # (frontend/app/package.json), NOT in frontend/package.json where -C alone
  # points. A filter therefore selects the manifest by package NAME and the -C
  # directory is not the answer.
  def check_package_scripts(script, base, repo)
    script.scan(/pnpm((?:\s+(?:-\w|--\S+)(?:[ =]\S+)?)*)\s+run\s+([A-Za-z0-9:._-]+)/) do |flags, name|
      manifest = manifest_for(flags.to_s, base, repo)
      if manifest.nil?
        record(STATUS_UNVERIFIABLE, "pnpm-script", name,
               "could not resolve which package.json `pnpm#{flags}` selects")
        next
      end
      verify(repo, "pnpm-script", "#{manifest} -> #{name}") do
        body = repo.read(manifest)
        body && (JSON.parse(body)["scripts"] || {}).key?(name)
      end
    end
  end

  # A workspace filter wins over -C: -C only says where pnpm starts looking for
  # the workspace, the filter says which package inside it actually runs.
  def manifest_for(flags, base, repo)
    filter = flags.match(/(?:--filter|-F)[ =](\S+)/) ? Regexp.last_match(1) : nil
    return workspace_manifest(filter, repo) if filter

    directory = flags.match(/(?:--dir|-C)[ =](\S+)/) ? Regexp.last_match(1) : base
    join(directory, "package.json")
  end

  def workspace_manifest(package_name, repo)
    return nil unless repo.available?

    repo.package_manifests.find do |manifest|
      body = repo.read(manifest)
      body && JSON.parse(body)["name"] == package_name
    rescue JSON::ParserError
      false
    end
  end

  def check_node_scripts(script, base, repo)
    script.scan(/\bnode\s+(\S+\.(?:mjs|cjs|js))/) do |(relative)|
      next if relative.start_with?("-") || relative.include?("${{")

      target = join(base, relative)
      verify(repo, "node-script", target) { repo.file?(target) }
    end
  end

  def check_poe_tasks(script, base, repo)
    script.scan(/uv\s+run\s+poe\s+([A-Za-z0-9:._-]+)/) do |(task)|
      manifest = join(base, "pyproject.toml")
      verify(repo, "poe-task", "#{manifest} -> #{task}") do
        body = repo.read(manifest)
        body && body.include?("[tool.poe.tasks") && body.match?(/^\s*"?#{Regexp.escape(task)}"?\s*=|^\[tool\.poe\.tasks\.#{Regexp.escape(task)}\]/)
      end
    end
  end

  def join(base, relative)
    return relative if base.nil? || base.empty? || base == "."

    File.join(base, relative)
  end

  # The single place a consumer-side answer is produced, so "no clone" can never
  # be mistaken for "resolved".
  def verify(repo, kind, reference)
    unless repo.available?
      record(STATUS_UNVERIFIABLE, kind, reference, repo.describe)
      return
    end

    if yield
      record(STATUS_OK, kind, reference, "resolves in #{repo.name}")
    else
      record(STATUS_MISSING, kind, reference, "does not resolve in #{repo.name} (#{repo.describe})")
    end
  end
end

def locate_consumer(root, name)
  DIRECTORY_ALIASES.fetch(name, [name]).each do |candidate|
    path = File.join(root, candidate)
    return path if Dir.exist?(path)
  end
  nil
end

def parse_arguments(argv)
  consumer_root = File.expand_path("~/dev/oss")
  ci_root = File.expand_path(File.join(__dir__, "..", ".."))
  paths = []

  until argv.empty?
    case (argument = argv.shift)
    when "--consumer-root" then consumer_root = argv.shift.to_s
    when "--ci-root" then ci_root = argv.shift.to_s
    else paths << argument
    end
  end
  [consumer_root, ci_root, paths]
end

consumer_root, ci_root, example_paths = parse_arguments(ARGV)
repos = {}

example_paths.each do |path|
  example = Example.new(path, ci_root)
  name = example.consumer_name
  repos[name] ||= ConsumerRepo.new(name, locate_consumer(consumer_root, name))
  example.references(repos[name]).each { |row| puts row.join("\t") }
rescue StandardError => error
  # A malformed example is itself a finding — never a skipped file.
  puts [path, STATUS_MISSING, "parse", File.basename(path), error.message].join("\t")
end
