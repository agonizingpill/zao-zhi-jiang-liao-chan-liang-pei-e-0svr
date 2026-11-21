"""Exercise the product's public API with isolated demo data."""

from dataclasses import dataclass
from datetime import date
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API = os.environ.get('PULP_VERIFY_API', 'http://127.0.0.1:19266')


@dataclass
class Client:
    token: str = ''

    def call(self, path, payload=None, status=200):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        request = Request(API + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=12) as response:
                code = response.status
                body = self.decode(response)
        except HTTPError as error:
            code = error.code
            body = self.decode(error)
        if status is not None:
            assert code == status, f'{path}: expected HTTP {status}, got {code}: {body}'
            return body
        return code, body

    @staticmethod
    def decode(response):
        raw = response.read().decode(errors='replace')
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


def supervisor():
    row = Client().call('/auth/login', {'email': 'supervisor@riverpaper.test', 'password': 'RiverSup2026!'})
    return Client(row['access_token'])


def ensure_run(client, code='PM-12-QA-WINTER', capacity=80, steam=470, dryer=109):
    runs = client.call('/machine-runs')
    original = next(row for row in runs if row['run_code'] == 'PM-12-2611')
    existing = next((row for row in runs if row['run_code'] == code), None)
    if existing:
        return original, existing
    alternative = client.call('/machine-runs', {
        'run_code': code,
        'machine_name': original['machine_name'],
        'line_speed_mpm': 73,
        'basis_weight_gsm': 72,
        'dryer_temp_c': dryer,
        'steam_pressure_kpa': steam,
        'safety_capacity_tons': capacity,
    })
    return original, alternative


def create_split_candidate(client, code, week):
    plans = client.call('/quota-plans')
    same = next((row for row in plans if row['plan_code'] == code), None)
    if same:
        return same
    lot = next(row for row in client.call('/pulp-lots') if row['lot_code'] == 'PL-261121-03')
    plan = client.call('/quota-plans/forecast', {
        'plan_code': code,
        'pulp_lot_id': lot['id'],
        'week_start': date.fromisoformat(week).isoformat(),
        'safety_limit_tons': 128,
    })
    for _ in range(48):
        row = next(p for p in client.call('/quota-plans') if p['id'] == plan['id'])
        if row['status'] != 'queued':
            assert row['status'] == 'split_required', row
            return row
        time.sleep(0.25)
    raise AssertionError(f'{code}: forecast stayed queued')


if __name__ == '__main__':
    client = supervisor()
    first, second = ensure_run(client)
    print(f"同一纸机的两套工况已就绪：{first['run_code']}、{second['run_code']}")
