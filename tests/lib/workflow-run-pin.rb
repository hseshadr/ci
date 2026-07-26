# Decide whether ONE workflow file's fork-deploy gate actually holds.
#
# WHY THIS EXISTS: `workflow_run` runs in the BASE repository with the BASE
# repository's secrets, and every `head_*` field on the event is attacker-
# controlled — a fork's default branch is also called `main`, so a fork-PR run
# satisfies both a `branches: [main]` trigger filter and a
# `head_branch == 'main'` gate. The only trust boundary is
# `head_repository.full_name == github.repository`, plus `event == 'push'`
# (a fork PR produces a `pull_request` workflow_run) and
# `conclusion == 'success'`.
#
# WHY IT IS A PARSE AND NOT A SUBSTRING TEST: the check this replaces asked
# whether two substrings appeared anywhere in any `if:`. That blessed all five
# of these, each of which deploys fork code with the base repo's credentials:
#
#   1. `head_repository.full_name != github.repository`  (inverted)
#   2. `... == github.repository || github.run_id != ''` (ORed away)
#   3. the pin in job A, the secrets in unrelated job B   (wrong job)
#   4. a gate with no `conclusion == 'success'` at all    (red CI still deploys)
#   5. a gate with no `event == 'push'`                   (fork PR still deploys)
#
# So the expression is parsed into a boolean AST and the three properties are
# proven by exhaustion: for each property P, is there ANY assignment of the
# gate's other terms under which the job runs while P is false? If yes, the gate
# does not require P — no matter how the text is spelled.
#
# SCOPE: a file is judged when it is triggered by `workflow_run`, OR when it is
# a reusable (`workflow_call`) workflow that CONSUMES `github.event.workflow_run.*`
# — because such a workflow is designed to be called from a workflow_run trigger
# and carries the same exposure. Skipping the second case is why the pin could be
# deleted from cloudflare-pages-deploy.yml with the suite still green.
#
# EVERY job must be gated, not merely the one that names a secret today: adding
# `secrets:` to an ungated job is a one-line diff that re-examines no guard.
# A job inherits the gates of every job it (transitively) `needs`, because a
# skipped need skips the dependent — unless the dependent opts out with
# `always()` / `!cancelled()`, in which case only its own `if` counts.
#
# Exit status: 0 = out of scope, 1 = correctly pinned, 2 = UNPINNED (reasons on
# stdout, one per line).
require "yaml"

MAX_FREE_TERMS = 14

# --- expression front end ----------------------------------------------------

def tokenize(text)
  tokens = []
  index = 0
  while index < text.length
    index = scan_token(text, index, tokens)
  end
  tokens
end

OPERATORS = { "&&" => :and, "||" => :or, "==" => :eq, "!=" => :ne }.freeze
PUNCTUATION = { "!" => :not, "(" => :lparen, ")" => :rparen }.freeze

def scan_token(text, index, tokens)
  char = text[index]
  return index + 1 if char =~ /\s/

  operator = OPERATORS[text[index, 2]]
  if operator
    tokens << [operator]
    return index + 2
  end

  punctuation = PUNCTUATION[char]
  if punctuation
    tokens << [punctuation]
    return index + 1
  end

  return scan_string(text, index, tokens) if char == "'" || char == '"'

  scan_word(text, index, tokens)
end

# String literals are normalised to single quotes so `"push"` and `'push'` are
# the same term.
def scan_string(text, index, tokens)
  closing = text.index(text[index], index + 1)
  raise "unterminated string literal" if closing.nil?

  tokens << [:term, "'#{text[(index + 1)...closing]}'"]
  closing + 1
end

def scan_word(text, index, tokens)
  match = /\A[A-Za-z0-9_.\-\[\]*]+/.match(text[index..-1])
  raise "unexpected character #{text[index].inspect}" if match.nil?

  call_end = call_end_index(text, index + match[0].length)
  if call_end
    # A whole function call — `contains(a, b)`, `always()` — is ONE opaque term.
    tokens << [:term, text[index..call_end].gsub(/\s+/, "")]
    return call_end + 1
  end
  tokens << [:term, match[0]]
  index + match[0].length
end

# End index of a parenthesised call starting at/after `from`, or nil.
def call_end_index(text, from)
  cursor = from
  cursor += 1 while cursor < text.length && text[cursor] =~ /\s/
  return nil unless cursor < text.length && text[cursor] == "("

  depth = 0
  while cursor < text.length
    depth += 1 if text[cursor] == "("
    depth -= 1 if text[cursor] == ")"
    return cursor if depth.zero?

    cursor += 1
  end
  raise "unbalanced parentheses in call"
end

# Recursive-descent parser: or -> and -> unary -> primary(comparison).
class Parser
  def initialize(tokens)
    @tokens = tokens
    @position = 0
  end

  def parse
    node = parse_or
    raise "trailing tokens in expression" if @position < @tokens.length

    node
  end

  private

  def peek
    @tokens[@position]
  end

  def take
    token = @tokens[@position]
    @position += 1
    token
  end

  def parse_or
    node = parse_and
    while peek && peek[0] == :or
      take
      node = [:or, node, parse_and]
    end
    node
  end

  def parse_and
    node = parse_unary
    while peek && peek[0] == :and
      take
      node = [:and, node, parse_unary]
    end
    node
  end

  def parse_unary
    return [:not, (take && parse_unary)] if peek && peek[0] == :not

    parse_primary
  end

  def parse_primary
    return parse_group if peek && peek[0] == :lparen
    raise "expected a term" unless peek && peek[0] == :term

    parse_comparison(take[1])
  end

  def parse_group
    take
    node = parse_or
    raise "expected )" unless peek && peek[0] == :rparen

    take
    node
  end

  def parse_comparison(left)
    return term_node(left) unless peek && %i[eq ne].include?(peek[0])

    operator = take[0]
    raise "expected a term after #{operator}" unless peek && peek[0] == :term

    node = [:var, [left, take[1]].sort]
    operator == :ne ? [:not, node] : node
  end

  # `a != b` is exactly `!(a == b)`, which is what closes the inverted-pin
  # bypass: both spellings resolve to the same variable.
  def term_node(word)
    case word
    when "true", "always()" then [:const, true]
    when "false", "cancelled()" then [:const, false]
    else [:var, [word]]
    end
  end
end

def parse_condition(text)
  stripped = text.to_s.strip
  stripped = stripped[3..-3].to_s.strip if stripped.start_with?("${{") && stripped.end_with?("}}")
  return [:const, true] if stripped.empty?

  Parser.new(tokenize(stripped)).parse
end

# --- the three properties ----------------------------------------------------

REPO_PIN = ["github.event.workflow_run.head_repository.full_name", "github.repository"].sort.freeze
PUSH_PIN = ["github.event.workflow_run.event", "'push'"].sort.freeze
SUCCESS_PIN = ["github.event.workflow_run.conclusion", "'success'"].sort.freeze

PROPERTIES = {
  repo: "github.event.workflow_run.head_repository.full_name == github.repository",
  push: "github.event.workflow_run.event == 'push'",
  success: "github.event.workflow_run.conclusion == 'success'"
}.freeze

def classify(key)
  return :repo if key == REPO_PIN
  return :push if key == PUSH_PIN
  return :success if key == SUCCESS_PIN
  return :free unless key.length == 2 && key.include?("github.event_name")

  key.include?("'workflow_run'") ? :event_is_workflow_run : :event_is_other
end

def collect_variables(node, found = [])
  case node[0]
  when :var then found << node[1] unless found.include?(node[1])
  when :not then collect_variables(node[1], found)
  when :and, :or
    collect_variables(node[1], found)
    collect_variables(node[2], found)
  end
  found
end

def evaluate(node, environment)
  case node[0]
  when :const then node[1]
  when :var then environment.fetch(node[1])
  when :not then !evaluate(node[1], environment)
  when :and then evaluate(node[1], environment) && evaluate(node[2], environment)
  else evaluate(node[1], environment) || evaluate(node[2], environment)
  end
end

# Split the gate's terms into the ones the fork scenario fixes and the ones we
# must exhaust. `target` is forced false — that IS the attack.
def partition_terms(node, target)
  fixed = {}
  free = []
  collect_variables(node).each do |key|
    case classify(key)
    when :repo, :push, :success
      classify(key) == target ? fixed[key] = false : free << key
    when :event_is_workflow_run then fixed[key] = true
    when :event_is_other then fixed[key] = false
    else free << key
    end
  end
  [fixed, free]
end

# True when the gate can still fire while `target` is false — i.e. it does not
# require the property.
def satisfiable_without?(node, target)
  fixed, free = partition_terms(node, target)
  return true if free.length > MAX_FREE_TERMS

  (0...(1 << free.length)).any? do |mask|
    environment = fixed.dup
    free.each_with_index { |key, bit| environment[key] = mask[bit] == 1 }
    evaluate(node, environment)
  end
end

# --- workflow model ----------------------------------------------------------

def triggers(document)
  value = document["on"]
  value = document[true] if value.nil?
  value.is_a?(Hash) ? value : {}
end

def string_values(value, collected = [])
  case value
  when String then collected << value
  when Hash then value.each_value { |child| string_values(child, collected) }
  when Array then value.each { |child| string_values(child, collected) }
  end
  collected
end

def in_scope?(document)
  keys = triggers(document).keys
  return true if keys.include?("workflow_run")
  return false unless keys.include?("workflow_call")

  string_values(document).any? { |text| text.include?("github.event.workflow_run.") }
end

# A job inherits its needs' gates, because a skipped need skips it too — unless
# it opts out of that with always()/cancelled(), in which case only its own
# `if` protects it.
def effective_condition(jobs, name, visiting = [])
  job = jobs[name]
  return [:const, true] unless job.is_a?(Hash)
  return [:const, true] if visiting.include?(name)

  own_text = job["if"].to_s
  own = parse_condition(own_text)
  return own if own_text.match?(/\b(always|cancelled)\s*\(/)

  Array(job["needs"]).inject(own) do |node, need|
    [:and, node, effective_condition(jobs, need, visiting + [name])]
  end
end

def job_reasons(jobs, name)
  condition = effective_condition(jobs, name)
  PROPERTIES.keys.select { |target| satisfiable_without?(condition, target) }
            .map { |target| "job `#{name}` gate does not require #{PROPERTIES[target]}" }
rescue StandardError => error
  ["job `#{name}` gate could not be parsed (#{error.message}) — an unprovable gate is not a gate"]
end

def unpinned_reasons(document)
  jobs = document["jobs"]
  return ["workflow declares no jobs but consumes workflow_run data"] unless jobs.is_a?(Hash)

  jobs.keys.flat_map { |name| job_reasons(jobs, name) }
end

file = ARGV.fetch(0)
document = YAML.safe_load(File.read(file), aliases: true)
exit 0 unless document.is_a?(Hash) && in_scope?(document)

reasons = unpinned_reasons(document)
exit 1 if reasons.empty?

reasons.each { |reason| puts reason }
exit 2
