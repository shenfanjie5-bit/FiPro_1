from __future__ import annotations

from app.backtest import release_events as release_module


def test_record_and_query_release_events(tmp_path) -> None:
    event = release_module.record_release_event(
        {
            'skill_pack_id': 'cn_a_core',
            'target_version': '0.1.4',
            'champion_version_before': '0.1.3',
            'champion_version_after': '0.1.4',
            'switch_mode': 'promotion',
            'reason': 'promotion_gate_allow',
            'operator': 'promotion_engine',
            'dry_run': False,
            'executed': True,
        },
        root_dir=tmp_path,
    )
    assert event['event_id']
    assert event['executed'] is True

    listed = release_module.list_release_events(limit=10, offset=0, root_dir=tmp_path)
    assert listed['total'] == 1
    assert listed['items'][0]['event_id'] == event['event_id']

    fetched = release_module.get_release_event(event['event_id'], root_dir=tmp_path)
    assert fetched is not None
    assert fetched['champion_version_after'] == '0.1.4'
