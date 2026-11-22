"""Tests for config loading and the minimal YAML subset parser."""

import pytest

from pr_sentinel.config import (
    Config,
    ConfigError,
    api_key_from_env,
    load_config,
    parse_config_text,
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


def test_api_key_from_env():
    assert api_key_from_env({}) is None
    assert api_key_from_env({"PR_SENTINEL_API_KEY": "sk-fake-test"}) == "sk-fake-test"
    assert api_key_from_env({"PR_SENTINEL_API_KEY": ""}) is None
