"""Tests for config loading and the minimal YAML subset parser."""

import pytest

from pr_sentinel.config import (
    MAX_INSTRUCTIONS_CHARS,
    Config,
    ConfigError,
    api_key_from_env,
    load_config,
    parse_config_text,
    resolve_instructions,
)


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config == Config()
    assert config.model == "gpt-5"
    assert config.severity_threshold == "medium"
    assert config.ignore_paths == []
    assert config.max_files == 50
    assert config.max_diff_chars == 100000
    assert config.mode == "inline"
    assert config.max_inline_comments == 20
    assert config.api_base_url == "https://api.openai.com/v1"


def test_parse_empty_text_gives_defaults():
    assert parse_config_text("") == Config()
    assert parse_config_text("# only a comment\n") == Config()


def test_sequence_items_with_nested_mappings():
    # Regression test: "- key: value" items whose mapping continues on the
    # following lines (as in GitHub workflow files) must all be parsed.
    from pr_sentinel.config import parse_yaml_subset

    text = """jobs:
  test:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pytest
"""
    data = parse_yaml_subset(text)
    steps = data["jobs"]["test"]["steps"]
    assert steps[0] == {"uses": "actions/checkout@v4"}
    assert steps[1] == {
        "uses": "actions/setup-python@v5",
        "with": {"python-version": "3.12"},
    }
    assert steps[2] == {"run": "pytest"}


def test_valid_yaml_scalars_lists_quoted_strings_comments():
    text = """
# top comment
model: "gpt-5-mini"  # trailing comment
severity_threshold: 'high'
max_files: 10
max_diff_chars: 5000
mode: summary
max_inline_comments: 3
api_base_url: 'https://relay.example.com/v1'
ignore_paths:
  - "vendor/**"
  - '*.min.js'
  - docs/**
"""
    config = parse_config_text(text)
    assert config.model == "gpt-5-mini"
    assert config.severity_threshold == "high"
    assert config.max_files == 10
    assert config.max_diff_chars == 5000
    assert config.mode == "summary"
    assert config.max_inline_comments == 3
    assert config.api_base_url == "https://relay.example.com/v1"
    assert config.ignore_paths == ["vendor/**", "*.min.js", "docs/**"]


def test_flow_list_and_quoted_hash():
    config = parse_config_text('ignore_paths: ["a/**", "b/**"]\nmodel: "weird#model"\n')
    assert config.ignore_paths == ["a/**", "b/**"]
    assert config.model == "weird#model"


def test_block_scalars():
    from pr_sentinel.config import parse_yaml_subset

    text = """job:
  run: |
    echo one
    echo two
  folded: >-
    hello
    world
"""
    data = parse_yaml_subset(text)
    assert data["job"]["run"] == "echo one\necho two\n"
    assert data["job"]["folded"] == "hello world"


def test_load_config_from_file(tmp_path):
    path = tmp_path / ".pr-sentinel.yml"
    path.write_text("model: test-model\nmode: summary\n", encoding="utf-8")
    config = load_config(str(path))
    assert config.model == "test-model"
    assert config.mode == "summary"
    assert config.severity_threshold == "medium"


def test_missing_file_error():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/definitely/not/here/.pr-sentinel.yml")


def test_unknown_key_error():
    with pytest.raises(ConfigError, match="unknown config keys"):
        parse_config_text("model: x\nbogus_key: 1\n")


def test_invalid_severity_error():
    with pytest.raises(ConfigError, match="severity_threshold"):
        parse_config_text("severity_threshold: fatal\n")


def test_invalid_mode_error():
    with pytest.raises(ConfigError, match="mode must be"):
        parse_config_text("mode: chatty\n")


def test_invalid_int_error():
    with pytest.raises(ConfigError, match="max_files"):
        parse_config_text("max_files: 0\n")
    with pytest.raises(ConfigError, match="max_files"):
        parse_config_text("max_files: many\n")


def test_api_base_url_validation():
    assert (
        parse_config_text("api_base_url: https://api.example.com/v1\n").api_base_url
        == "https://api.example.com/v1"
    )
    # Plain http:// is still allowed: local fake servers in the tests use
    # it, and the CLI warns about it when the key is actually sent.
    assert (
        parse_config_text("api_base_url: http://localhost:9/v1\n").api_base_url
        == "http://localhost:9/v1"
    )
    for bad in ["httpfoo", "", "ftp://example.com/v1", "http://", "://no-scheme"]:
        with pytest.raises(ConfigError, match="api_base_url"):
            parse_config_text(f"api_base_url: {bad}\n")


def test_escaped_quote_before_hash_does_not_truncate():
    # An escaped quote inside a double-quoted string must not toggle the
    # quote state: the # after the closing quote is a comment, not data.
    config = parse_config_text('model: "a\\"b" # trailing comment\n')
    assert config.model == 'a"b'


def test_api_key_from_env():
    assert api_key_from_env({}) is None
    assert api_key_from_env({"PR_SENTINEL_API_KEY": "sk-fake-test"}) == "sk-fake-test"
    assert api_key_from_env({"PR_SENTINEL_API_KEY": ""}) is None


# --- fail_on_severity (v0.2.0) ----------------------------------------------


def test_fail_on_severity_default_is_none():
    assert Config().fail_on_severity is None
    assert parse_config_text("").fail_on_severity is None


def test_fail_on_severity_valid_values():
    for value in ("critical", "high", "medium", "low"):
        config = parse_config_text(f"fail_on_severity: {value}\n")
        assert config.fail_on_severity == value


def test_fail_on_severity_none_spelling_is_normalized():
    assert parse_config_text("fail_on_severity: none\n").fail_on_severity is None
    assert Config(fail_on_severity="none").fail_on_severity is None


def test_fail_on_severity_empty_string_is_none():
    """A blank gate (empty string, e.g. from an empty Action input)
    means "no gate", just like "none"."""
    assert parse_config_text("fail_on_severity: ''\n").fail_on_severity is None
    assert Config(fail_on_severity="").fail_on_severity is None
    assert Config(fail_on_severity="   ").fail_on_severity is None


def test_fail_on_severity_case_and_whitespace_are_normalized():
    assert parse_config_text("fail_on_severity: HIGH\n").fail_on_severity == "high"
    assert Config(fail_on_severity="  Low").fail_on_severity == "low"
    assert Config(fail_on_severity=" None ").fail_on_severity is None


def test_fail_on_severity_invalid_value():
    with pytest.raises(ConfigError, match="fail_on_severity"):
        parse_config_text("fail_on_severity: urgent\n")
    with pytest.raises(ConfigError, match="fail_on_severity"):
        parse_config_text("fail_on_severity: 3\n")


# --- api_format (v0.3.0) ---------------------------------------------------


def test_api_format_default_is_responses():
    assert Config().api_format == "responses"
    assert parse_config_text("").api_format == "responses"


def test_api_format_valid_values_are_normalized():
    assert (
        parse_config_text("api_format: chat_completions\n").api_format
        == "chat_completions"
    )
    assert (
        parse_config_text("api_format: Chat_Completions\n").api_format
        == "chat_completions"
    )
    assert Config(api_format=" RESPONSES ").api_format == "responses"


def test_api_format_invalid_value():
    with pytest.raises(ConfigError, match="api_format"):
        parse_config_text("api_format: rest\n")
    with pytest.raises(ConfigError, match="api_format"):
        parse_config_text("api_format: ''\n")


def test_api_format_wrong_type_is_rejected():
    config = Config(api_format=3)
    with pytest.raises(ConfigError, match="api_format"):
        config.validate()


# --- instructions / instructions_file (v0.3.0) ------------------------------


def test_instructions_defaults_are_none():
    assert Config().instructions is None
    assert Config().instructions_file is None
    assert resolve_instructions(Config()) is None


def test_instructions_inline():
    config = parse_config_text('instructions: "Focus on security."\n')
    assert config.instructions == "Focus on security."
    assert resolve_instructions(config) == "Focus on security."


def test_instructions_blank_string_is_none():
    assert parse_config_text("instructions: ''\n").instructions is None
    assert Config(instructions="   ").instructions is None


def test_instructions_file(tmp_path, monkeypatch):
    # The file is resolved relative to the config file's directory,
    # not the working directory.
    subdir = tmp_path / "config"
    subdir.mkdir()
    rules = subdir / "review-rules.md"
    rules.write_text("Never suggest tabs.\n", encoding="utf-8")
    config_path = subdir / ".pr-sentinel.yml"
    config_path.write_text("instructions_file: review-rules.md\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = load_config(str(config_path))
    assert resolve_instructions(config) == "Never suggest tabs.\n"


def test_instructions_file_wins_over_inline(tmp_path):
    rules = tmp_path / "review-rules.md"
    rules.write_text("File wins.\n", encoding="utf-8")
    config = parse_config_text(
        "instructions: Inline.\ninstructions_file: review-rules.md\n",
        base_dir=str(tmp_path),
    )
    assert resolve_instructions(config) == "File wins.\n"


def test_instructions_file_missing_raises(tmp_path):
    config = Config(instructions_file="no-such-rules.md", config_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="instructions_file not found"):
        config.validate()


def test_instructions_file_absolute_path_rejected(tmp_path):
    # Absolute paths are never read, even when the file exists.
    rules = tmp_path / "review-rules.md"
    rules.write_text("Secret rules.\n", encoding="utf-8")
    config = Config(instructions_file=str(rules))
    with pytest.raises(ConfigError, match="must be a relative path"):
        config.validate()
    with pytest.raises(ConfigError, match="must be a relative path"):
        resolve_instructions(config)
    # /etc/passwd is the canonical traversal target.
    with pytest.raises(ConfigError, match="must be a relative path"):
        Config(instructions_file="/etc/passwd").validate()


def test_instructions_file_dotdot_escape_rejected(tmp_path):
    # A file that exists outside the config directory is still
    # rejected: ".." must not escape the config directory.
    outside = tmp_path / "outside.md"
    outside.write_text("Outside rules.\n", encoding="utf-8")
    subdir = tmp_path / "config"
    subdir.mkdir()
    config = Config(instructions_file="../outside.md", config_dir=str(subdir))
    with pytest.raises(ConfigError, match="escapes the config directory"):
        config.validate()
    with pytest.raises(ConfigError, match="escapes the config directory"):
        resolve_instructions(config)


def test_instructions_file_deleted_after_load_raises(tmp_path):
    rules = tmp_path / "review-rules.md"
    rules.write_text("Gone soon.\n", encoding="utf-8")
    config = Config(instructions_file="review-rules.md", config_dir=str(tmp_path))
    config.validate()
    rules.unlink()
    with pytest.raises(ConfigError, match="not readable"):
        resolve_instructions(config)


def test_instructions_file_invalid_utf8_raises(tmp_path):
    rules = tmp_path / "review-rules.md"
    rules.write_bytes(b"Not text: \xff\xfe\x00\n")
    config = Config(instructions_file="review-rules.md", config_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="not readable"):
        config.validate()
    with pytest.raises(ConfigError, match="not readable"):
        resolve_instructions(config)


def test_instructions_over_cap_raises():
    config = Config(instructions="x" * 4001)
    with pytest.raises(ConfigError, match="4000"):
        config.validate()
    # Exactly at the cap is fine.
    Config(instructions="x" * 4000).validate()


def test_instructions_file_over_cap_raises(tmp_path):
    rules = tmp_path / "review-rules.md"
    rules.write_text("x" * 4001, encoding="utf-8")
    config = Config(instructions_file="review-rules.md", config_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="4000"):
        config.validate()


def test_instructions_file_at_cap_loads(tmp_path):
    # Exactly at the cap is fine, on both the validate() and the
    # resolve_instructions() path.
    rules = tmp_path / "review-rules.md"
    rules.write_text("x" * MAX_INSTRUCTIONS_CHARS, encoding="utf-8")
    config = Config(instructions_file="review-rules.md", config_dir=str(tmp_path))
    config.validate()
    assert resolve_instructions(config) == "x" * MAX_INSTRUCTIONS_CHARS


def test_instructions_file_over_cap_raises_in_resolve(tmp_path):
    # The resolve path enforces the cap too, not just validate().
    rules = tmp_path / "review-rules.md"
    rules.write_text("x" * (MAX_INSTRUCTIONS_CHARS + 1), encoding="utf-8")
    config = Config(instructions_file="review-rules.md", config_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="4000"):
        resolve_instructions(config)


def test_instructions_file_read_is_bounded(tmp_path, monkeypatch):
    # The cap must be enforced by reading at most cap+1 characters:
    # spy on open() and assert no read of the instructions file ever
    # asks for more than the cap + 1 characters (and never an
    # unbounded read), on either the validate() or the resolve path.
    import builtins

    rules = tmp_path / "review-rules.md"
    rules.write_text("x" * 100_000, encoding="utf-8")
    real_open = builtins.open
    read_sizes: list[int] = []

    class _SpyHandle:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

        def read(self, size=-1):
            read_sizes.append(size)
            return self._handle.read(size)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def _spy_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        if str(file) == str(rules):
            return _SpyHandle(handle)
        return handle

    monkeypatch.setattr(builtins, "open", _spy_open)
    config = Config(instructions_file="review-rules.md", config_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="4000"):
        config.validate()
    assert read_sizes == [MAX_INSTRUCTIONS_CHARS + 1]
    read_sizes.clear()
    with pytest.raises(ConfigError, match="4000"):
        resolve_instructions(config)
    assert read_sizes == [MAX_INSTRUCTIONS_CHARS + 1]
