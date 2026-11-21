"""Check deployed split trials and read back decisions from the public API."""

from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
from threading import Barrier

from fixture import ensure_run, supervisor


ROUTE = '/split-trials'


def week(date, run, lower=None):
    data = {'week_start': date, 'machine_run_id': run['id']}
    if lower is not None:
        data['forecast_lower_tons'] = lower
    return data


def trial(client, code, order_tons, weeks):
    return client.call(ROUTE, {'order_code': code, 'order_tons': order_tons, 'weeks': weeks}, status=None)


def report(name, value):
    print(name, json.dumps(value, ensure_ascii=False, sort_keys=True))


def check_forecasts(client, first, alternative, suffix):
    supplied = [week('2026-12-07', first, 42), week('2026-12-14', first, 45)]
    status, result = trial(client, f'QA-DIFF-{suffix}', 70, supplied)
    report('同一纸机两个周次分别试算', {'http': status, 'status': result.get('status'), 'week_lower': [row['forecast_lower_tons'] for row in result.get('weeks', [])], 'allocated': [row['allocated_tons'] for row in result.get('weeks', [])], 'gap': result.get('total_gap_tons')})
    assert status == 201 and result['status'] == 'ready', result
    assert [row['allocated_tons'] for row in result['weeks']] == [42, 28], result

def check_retry(client, first, suffix):
    weeks = [week('2027-01-04', first, 35), week('2027-01-11', first, 40)]
    status, initial = trial(client, f'QA-RETRY-{suffix}', 160, weeks)
    assert status == 201, (status, initial)
    url = f"{ROUTE}/{initial['id']}"
    status, blocked = client.call(f'{url}/submit', {}, status=None)
    report('两周未排足时送审', {'http': status, 'result': blocked, 'gap': initial['total_gap_tons']})
    retried = client.call(f'{url}/retry', {'weeks': weeks})
    final = client.call(url)
    observation = {'before': [row['id'] for row in initial['weeks']], 'after': [row['id'] for row in final['weeks']], 'week_tons': [row['allocated_tons'] for row in final['weeks']], 'gap': final['total_gap_tons'], 'status': final['status']}
    report('原单重试后回读周计划', observation)
    assert initial['status'] == 'trial_failed' and status == 409, observation
    assert len(final['weeks']) == len(initial['weeks']) == 2 and retried['id'] == initial['id'], observation
    assert observation['week_tons'] == [35, 40] and observation['gap'] == 85, observation


def check_competing_approvals(client, first, suffix):
    data = [
        (f'QA-CAP-A-{suffix}', 100, [week('2027-02-01', first, 45), week('2027-02-15', first, 98)]),
        (f'QA-CAP-B-{suffix}', 105, [week('2027-02-08', first, 20), week('2027-02-15', first, 98)]),
    ]
    urls = []
    for code, amount, weeks in data:
        status, created = trial(client, code, amount, weeks)
        assert status == 201 and created['status'] == 'ready', (status, created)
        url = f"{ROUTE}/{created['id']}"
        urls.append(url)
        client.call(f'{url}/submit', {})
    start = Barrier(len(urls))

    def approve(url):
        start.wait(timeout=5)
        return client.call(f'{url}/approve', {}, status=None)

    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        responses = list(pool.map(approve, urls))
    observations = []
    for url, (status, response) in zip(urls, responses):
        final = client.call(url)
        observations.append({'code': final['order_code'], 'http': status, 'status': final['status'], 'detail': response.get('detail', '') if isinstance(response, dict) else response, 'occupied': [row['occupied'] for row in final['weeks']], 'week_tons': [row['allocated_tons'] for row in final['weeks']]})
    report('两单只争同一周时并发审批后的持久状态', observations)
    assert sorted(result['http'] for result in observations) == [200, 409], observations
    assert sorted(result['status'] for result in observations) == ['approved', 'returned'], observations
    assert sorted(result['occupied'] for result in observations) == [[False, False], [True, True]], observations


def main():
    client = supervisor()
    suffix = str(int(time.time()))[-6:]
    first, alternative = ensure_run(client)
    failures = 0
    checks = {'forecasts': lambda: check_forecasts(client, first, alternative, suffix), 'retry': lambda: check_retry(client, first, suffix), 'approvals': lambda: check_competing_approvals(client, first, suffix)}
    for name in sys.argv[1:] or checks.keys():
        try:
            checks[name]()
        except AssertionError as error:
            failures += 1
            print(f'FAIL {name}: {error}')
        else:
            print(f'PASS {name}')
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
