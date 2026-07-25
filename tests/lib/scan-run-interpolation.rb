# Report every `run:` block that pastes an attacker-influenceable GitHub
# expression straight into shell source.
#
# Emits one TAB-separated "<file>\t<vector>" line per finding, and nothing at all
# when a tree is clean. Parse errors are skipped rather than raised: validate_yaml
# already reports malformed YAML, and letting one bad file abort this sweep is
# precisely the fail-hard behaviour the suite was fixed to stop doing.
#
# Lives in its own file because the patterns contain `${{ ... }}`, which does not
# survive being embedded in a shell-quoted `ruby -e` string intact.

require "yaml"

# Ordered most-specific first so a single run block reports its clearest vector.
#
# GitHub's expression parser accepts ANY amount of whitespace after `${{` —
# `${{inputs.x}}`, `${{ inputs.x }}`, and `${{   inputs.x }}` all expand
# identically — so each vector matches `${{` followed by optional whitespace,
# never a fixed one-space literal (which two of these used to be, letting the
# no-space and extra-space spellings straight through).
VECTORS = [
  [/\$\{\{\s*inputs\./, "workflow inputs"],
  [/\$\{\{\s*github\.event\./, "github.event data (attacker-authored on a public repo)"],
  [/\$\{\{\s*github\.head_ref/, "github.head_ref (attacker-chosen branch name)"],
].freeze

def walk(value, &block)
  yield value if value.is_a?(Hash)
  children = value.is_a?(Hash) ? value.values : value
  children.each { |child| walk(child, &block) } if children.is_a?(Array)
end

ARGV.each do |file|
  begin
    document = YAML.safe_load(File.read(file), aliases: true)
  rescue StandardError
    next
  end

  found = []
  walk(document) do |node|
    script = node["run"]
    next unless script.is_a?(String)

    VECTORS.each do |pattern, vector|
      found << vector if script.match?(pattern) && !found.include?(vector)
    end
  end

  found.each { |vector| puts "#{file}\t#{vector}" }
end
