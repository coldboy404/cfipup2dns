import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'cfip_runner.py')
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def sync(self, existing, best, failure=None):
        calls = []
        def request(method, path, token, payload=None):
            calls.append((method, path, payload))
            if failure:
                raise RuntimeError(failure)
            return {'success': True}
        with patch.object(runner, 'resolve_zone_id', return_value='zone'), patch.object(runner, 'cf_request_all', return_value=existing), patch.object(runner, 'cf_request', side_effect=request):
            runner.update_dns_records('secret', [{'domain': 'cf.example.com'}], 'A', [{'ip': ip} for ip in best], 60, False)
        return calls

    def records(self):
        return [dict(id=str(i), type='A', name='cf.example.com', content=ip, ttl=60, proxied=False) for i, ip in enumerate(['1.1.1.1', '1.0.0.1'])]

    def test_reordered_ips_do_not_write(self):
        self.assertEqual(self.sync(self.records(), ['1.0.0.1', '1.1.1.1']), [])

    def test_reuses_only_obsolete_record(self):
        calls = self.sync(self.records(), ['8.8.8.8', '1.1.1.1'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'PATCH')
        self.assertTrue(calls[0][1].endswith('/1'))
        self.assertEqual(calls[0][2]['content'], '8.8.8.8')

    def test_empty_result_preserves_dns(self):
        self.assertEqual(self.sync(self.records(), []), [])

    def test_invalid_ip_rejected_before_api(self):
        with self.assertRaises(ValueError):
            self.sync(self.records(), ['not-an-ip'])
        with self.assertRaises(ValueError):
            self.sync(self.records(), ['2606:4700::1111'])

    def test_write_failure_prevents_deletion(self):
        with patch.object(runner, 'resolve_zone_id', return_value='zone'), patch.object(runner, 'cf_request_all', return_value=self.records()), patch.object(runner, 'cf_request', side_effect=RuntimeError('failed')) as request:
            with self.assertRaises(RuntimeError):
                runner.update_dns_records('token', [{'domain': 'cf.example.com'}], 'A', [{'ip': '8.8.8.8'}], 60, False)
            self.assertEqual([call.args[0] for call in request.call_args_list], ['PATCH'])

    def test_nonfinite_metrics_rejected(self):
        for value in ['nan', 'inf', '-inf']:
            self.assertIsNone(runner._float_or_none(value))

    def test_atomic_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            runner.atomic_json_write(path, {'result': [{'ip': '1.1.1.1'}]})
            self.assertEqual(json.loads(path.read_text())['result'][0]['ip'], '1.1.1.1')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob('.result-*')), [])

    def test_process_lock_blocks_overlapping_cli(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'PROJECT_DIR', Path(directory)), patch.object(runner, 'run_all_modes') as run:
            with open(Path(directory) / '.run.lock', 'a') as lock:
                runner.fcntl.flock(lock, runner.fcntl.LOCK_EX | runner.fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, '已有'):
                    runner.main()
            run.assert_not_called()
            runner.main()
            run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
