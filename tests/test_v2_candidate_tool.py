from pathlib import Path
import pytest

from tools.prepare_v2_candidate import prepare
from twitchbot.migration import CandidateImportError
from test_v2_candidate_importer import _fixture


def test_explicit_candidate_tool_verifies_without_overwriting(tmp_path):
    source, downloads, _, _ = _fixture(tmp_path)
    candidate = tmp_path/'new.sqlite3'
    result = prepare(source, downloads, candidate, 'fixture')
    assert result['result'] == 'verified' and result['source_unchanged']
    before = candidate.read_bytes()
    with pytest.raises(CandidateImportError, match='candidate_target_exists_or_unsafe'):
        prepare(source, downloads, candidate, 'fixture')
    assert candidate.read_bytes() == before


def test_candidate_tool_refuses_source_tree_destination(tmp_path):
    source, downloads, _, _ = _fixture(tmp_path)
    with pytest.raises(CandidateImportError):
        prepare(source, downloads, source/'new.sqlite3', 'fixture')
    assert not (source/'new.sqlite3').exists()
