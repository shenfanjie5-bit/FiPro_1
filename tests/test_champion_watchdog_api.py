from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.main import app


client = TestClient(app)


def test_watchdog_alert_and_ticket_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'list_champion_watchdog_alerts',
        lambda limit, offset, status: {  # noqa: ARG005
            'total': 1,
            'limit': limit,
            'offset': offset,
            'status_filter': status,
            'summary': {
                'open_count': 1,
                'acked_count': 0,
                'closed_count': 0,
                'critical_open_count': 1,
                'warning_open_count': 0,
            },
            'items': [
                {
                    'alert_id': 'wd_001_01_test',
                    'run_id': 'wd_001',
                    'severity': 'critical',
                    'code': 'TEST_ALERT',
                    'message': 'test',
                    'status': 'OPEN',
                }
            ],
        },
    )
    monkeypatch.setattr(
        routes_module,
        'get_champion_watchdog_alert',
        lambda alert_id: {
            'alert_id': alert_id,
            'run_id': 'wd_001',
            'severity': 'critical',
            'status': 'OPEN',
            'code': 'TEST_ALERT',
            'message': 'test',
        },
    )
    monkeypatch.setattr(
        routes_module,
        'acknowledge_champion_watchdog_alert',
        lambda alert_id, operator, note: {
            'alert_id': alert_id,
            'status': 'ACKED',
            'acknowledged_by': operator,
            'ack_note': note,
        },
    )
    monkeypatch.setattr(
        routes_module,
        'close_champion_watchdog_alert',
        lambda alert_id, operator, note: {
            'alert_id': alert_id,
            'status': 'CLOSED',
            'closed_by': operator,
            'close_note': note,
        },
    )
    monkeypatch.setattr(
        routes_module,
        'list_champion_watchdog_tickets',
        lambda limit, offset: {  # noqa: ARG005
            'total': 1,
            'limit': limit,
            'offset': offset,
            'items': [
                {
                    'ticket_id': 'wdt_001',
                    'status': 'OPEN',
                    'severity': 'critical',
                    'title': 'Champion Watchdog CRITICAL alert',
                    'run_id': 'wd_001',
                    'alert_count': 1,
                    'alert_ids': ['wd_001_01_test'],
                }
            ],
        },
    )
    monkeypatch.setattr(
        routes_module,
        'get_champion_watchdog_ticket',
        lambda ticket_id: {
            'ticket_id': ticket_id,
            'status': 'OPEN',
            'severity': 'critical',
            'title': 'Champion Watchdog CRITICAL alert',
            'run_id': 'wd_001',
            'alert_count': 1,
            'alert_ids': ['wd_001_01_test'],
        },
    )

    resp_alerts = client.get('/skill-packs/champion/watchdog/alerts?limit=20&offset=0&status=OPEN')
    assert resp_alerts.status_code == 200
    assert resp_alerts.json()['total'] == 1

    resp_alert = client.get('/skill-packs/champion/watchdog/alerts/wd_001_01_test')
    assert resp_alert.status_code == 200
    assert resp_alert.json()['alert_id'] == 'wd_001_01_test'

    resp_ack = client.post(
        '/skill-packs/champion/watchdog/alerts/wd_001_01_test/ack',
        json={'operator': 'tester', 'note': 'ack'},
    )
    assert resp_ack.status_code == 200
    assert resp_ack.json()['status'] == 'ACKED'

    resp_close = client.post(
        '/skill-packs/champion/watchdog/alerts/wd_001_01_test/close',
        json={'operator': 'tester', 'note': 'close'},
    )
    assert resp_close.status_code == 200
    assert resp_close.json()['status'] == 'CLOSED'

    resp_tickets = client.get('/skill-packs/champion/watchdog/tickets?limit=20&offset=0')
    assert resp_tickets.status_code == 200
    assert resp_tickets.json()['total'] == 1

    resp_ticket = client.get('/skill-packs/champion/watchdog/tickets/wdt_001')
    assert resp_ticket.status_code == 200
    assert resp_ticket.json()['ticket_id'] == 'wdt_001'


def test_watchdog_alert_endpoints_return_404(monkeypatch) -> None:
    monkeypatch.setattr(routes_module, 'get_champion_watchdog_alert', lambda alert_id: None)  # noqa: ARG005
    monkeypatch.setattr(
        routes_module,
        'acknowledge_champion_watchdog_alert',
        lambda alert_id, operator, note: (_ for _ in ()).throw(ValueError('champion watchdog alert not found')),  # noqa: ARG005
    )
    monkeypatch.setattr(
        routes_module,
        'close_champion_watchdog_alert',
        lambda alert_id, operator, note: (_ for _ in ()).throw(ValueError('champion watchdog alert not found')),  # noqa: ARG005
    )

    resp_get = client.get('/skill-packs/champion/watchdog/alerts/unknown_alert')
    assert resp_get.status_code == 404

    resp_ack = client.post(
        '/skill-packs/champion/watchdog/alerts/unknown_alert/ack',
        json={'operator': 'tester', 'note': ''},
    )
    assert resp_ack.status_code == 404

    resp_close = client.post(
        '/skill-packs/champion/watchdog/alerts/unknown_alert/close',
        json={'operator': 'tester', 'note': ''},
    )
    assert resp_close.status_code == 404
