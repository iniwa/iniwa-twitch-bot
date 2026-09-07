import json

import pytest

from tools.stage_v2_source import StagingError, stage


def source(tmp_path):
    root=tmp_path/'source';root.mkdir()
    (root/'history').mkdir()
    (root/'config.json').write_text(json.dumps({'access_token':'syntheticsecret','broadcaster_token':'othersecret','rules':[],'is_running':False}))
    (root/'viewers.json').write_text('{}')
    (root/'history/stream_index.json').write_text('{}')
    (root/'history/stream_fixture.jsonl').write_text('{"messages":[]}\n')
    return root


def test_staging_removes_credentials_and_preserves_source(tmp_path):
    root=source(tmp_path)
    before=(root/'config.json').read_bytes()
    (root/'private-secret.json').write_text('uncopiedsecret')
    target=tmp_path/'staged'
    result=stage(root,target)
    assert result['state']=='staged' and result['files']==4
    assert result['credential_fields_removed']==2 and result['ignored_entries']==1
    assert json.loads((target/'config.json').read_text())=={'rules':[],'is_running':False}
    assert (root/'config.json').read_bytes()==before
    assert not (target/'private-secret.json').exists()
    assert not (target/'.staging-incomplete').exists()
    assert (target/'history/stream_fixture.jsonl').read_bytes()==(root/'history/stream_fixture.jsonl').read_bytes()
    with pytest.raises(StagingError,match='unsafe_staging_target'): stage(root,target)


def test_changed_source_leaves_marker_and_is_rejected_by_import_inspector(tmp_path,monkeypatch):
    from tools import stage_v2_source as tool
    from twitchbot.migration import LegacySourceInspector
    root=source(tmp_path);target=tmp_path/'staged'
    original=tool._read
    def changing(path):
        content=original(path)
        if path.name=='config.json': (root/'viewers.json').write_text('{"456":{}}')
        return content
    monkeypatch.setattr(tool,'_read',changing)
    with pytest.raises(StagingError,match='source_changed'):stage(root,target)
    assert (target/'.staging-incomplete').exists()
    downloads=tmp_path/'downloads';downloads.mkdir()
    report=LegacySourceInspector(target,downloads,'fixture').inspect()
    assert report.unsupported


@pytest.mark.parametrize('configuration', ['{"is_running":false,"is_running":true}', '[]'])
def test_invalid_configuration_is_never_written(tmp_path,configuration):
    root=source(tmp_path)
    (root/'config.json').write_text(configuration)
    target=tmp_path/'staged'
    with pytest.raises(StagingError):stage(root,target)
    assert not (target/'config.json').exists()
    assert (target/'.staging-incomplete').exists()


def test_staging_refuses_source_descendant_or_over_limit(tmp_path,monkeypatch):
    from tools import stage_v2_source as tool
    root=source(tmp_path)
    with pytest.raises(StagingError,match='unsafe_staging_target'):stage(root,root/'staged')
    monkeypatch.setattr(tool,'MAX_TOTAL',1)
    with pytest.raises(StagingError,match='source_limit_exceeded'):stage(root,tmp_path/'staged')
    assert not (tmp_path/'staged').exists()


def test_nested_credentials_are_removed_but_unknown_nonsecret_settings_remain(tmp_path):
    root=source(tmp_path)
    (root/'config.json').write_text(json.dumps({'integration':{'token':'private','clientSecret':'private','secretary_bot_url':'fixture-url','enabled':True},'items':[{'refresh_token':'private','name':'keep'}]}))
    target=tmp_path/'staged'
    assert stage(root,target)['credential_fields_removed']==3
    clean=json.loads((target/'config.json').read_text())
    assert clean=={'integration':{'secretary_bot_url':'fixture-url','enabled':True},'items':[{'name':'keep'}]}
    assert 'private' not in (target/'config.json').read_text()
