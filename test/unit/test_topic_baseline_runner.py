from pathlib import Path

import pytest

from evaluation.topic_modelling.run_baselines import (
    BaselineSettings,
    discover_corpora,
    parse_args,
    validate_settings,
)


def settings(**overrides: object) -> BaselineSettings:
    values = {
        "models": ("lda", "lsi"),
        "topic_counts": (2, 3),
        "seeds": (11, 22),
        "lsi_ranks": (25,),
        "lda_max_iter": 100,
        "max_df": 0.85,
        "top_n_terms": 10,
        "minilm_model": "all-MiniLM-L6-v2",
    }
    values.update(overrides)
    return BaselineSettings(**values)


def test_baseline_defaults_preserve_shared_vocabulary_filter() -> None:
    args = parse_args([])
    assert args.models == ("lda", "lsi")
    assert args.max_df == 0.85
    assert args.topic_counts == (2, 3, 4, 5, 6, 8, 10)
    assert args.seeds == (11, 22, 33)


@pytest.mark.parametrize(
    "overrides",
    [
        {"topic_counts": (1,)},
        {"seeds": ()},
        {"lsi_ranks": (0,)},
        {"max_df": 0.0},
        {"max_df": 1.1},
        {"top_n_terms": 0},
    ],
)
def test_invalid_baseline_settings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_settings(settings(**overrides))


def test_discover_corpora_honours_explicit_project_order(tmp_path: Path) -> None:
    for project in ("second", "first"):
        corpus = tmp_path / project
        corpus.mkdir()
        (corpus / "documents.jsonl").write_text("{}\n", encoding="utf-8")

    assert discover_corpora(tmp_path, ["first", "second"]) == [
        tmp_path / "first",
        tmp_path / "second",
    ]


def test_discover_corpora_reports_missing_project(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing frozen corpus"):
        discover_corpora(tmp_path, ["missing"])
