from pathlib import Path

import pytest

from app.evals.corpus import CorpusError, EvalCase, load_case, load_corpus

CASE_YAML = """\
id: t1-999
domain: site_layout
tier: t1
smoke: true
prompt: "一个简单住宅组团:3栋6层板楼、一条环路、中心绿地"
required_roles: [plot, building]
min_objects: 10
timeout_s: 600
"""


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_load_case_parses_all_fields(tmp_path):
    path = _write(tmp_path, "t1/t1-999.yaml", CASE_YAML)
    case = load_case(path)
    assert case == EvalCase(
        id="t1-999",
        domain="site_layout",
        tier="t1",
        prompt="一个简单住宅组团:3栋6层板楼、一条环路、中心绿地",
        required_roles=("plot", "building"),
        min_objects=10,
        smoke=True,
        timeout_s=600,
    )


def test_load_case_defaults(tmp_path):
    path = _write(
        tmp_path,
        "m.yaml",
        'id: m1-999\ndomain: mechanical\ntier: t1\nprompt: "一个M8法兰"\n',
    )
    case = load_case(path)
    assert case.required_roles == ()
    assert case.min_objects is None
    assert case.smoke is False
    assert case.timeout_s == 900


@pytest.mark.parametrize(
    "bad",
    [
        "id: x\ndomain: nope\ntier: t1\nprompt: p\n",
        "id: x\ndomain: mechanical\ntier: t9\nprompt: p\n",
        "id: x\ndomain: site_layout\ntier: t1\nprompt: p\nrequired_roles: [castle]\n",
        "domain: mechanical\ntier: t1\nprompt: p\n",
        "id: x\ndomain: mechanical\ntier: t1\nprompt: ''\n",
    ],
)
def test_load_case_rejects_invalid(tmp_path, bad):
    path = _write(tmp_path, "bad.yaml", bad)
    with pytest.raises(CorpusError):
        load_case(path)


def test_load_corpus_sorted_and_unique(tmp_path):
    _write(tmp_path, "a/z.yaml", 'id: b-2\ndomain: mechanical\ntier: t1\nprompt: p\n')
    _write(tmp_path, "b/a.yaml", 'id: a-1\ndomain: mechanical\ntier: t1\nprompt: p\n')
    cases = load_corpus(tmp_path)
    assert [c.id for c in cases] == ["a-1", "b-2"]


def test_load_corpus_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "a.yaml", 'id: dup\ndomain: mechanical\ntier: t1\nprompt: p\n')
    _write(tmp_path, "b.yaml", 'id: dup\ndomain: mechanical\ntier: t1\nprompt: q\n')
    with pytest.raises(CorpusError):
        load_corpus(tmp_path)
