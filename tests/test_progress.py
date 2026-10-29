import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_runner import runner
from test_selection import probe_result


class ProgressTests(unittest.TestCase):
    def test_duration_legacy_go_spellings(self):
        for value, expected in [('.5s', .5), ('1.s', 1), ('+1s', 1)]:
            self.assertEqual(runner.duration_seconds(value), expected)

    def test_deadline_drains_rows_already_emitted(self):
        def delayed(message):
            if 'MCIS progress' in message:
                time.sleep(.15)
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'log', side_effect=delayed):
            path = Path(tmp) / 'scan'
            source = 'import os,time;os.write(1,b"progress: 1/2 done\\n");time.sleep(.02);os.write(1,b\'{"ip":"192.0.2.1","ok":true,"score_ms":1}\\n\');time.sleep(5)'
            runner.run_mcis([sys.executable, '-c', source], path, '4', .1)
            self.assertEqual(len(runner.parse_json_lines(path)[1]), 1)

    def test_all_mode_samples_attempt_pool_even_after_target(self):
        rows = [dict(ip=f'192.0.2.{i}', ok=True, score_ms=i) for i in range(1, 7)]
        with patch.object(runner, 'DOWNLOAD_MODE', 'all'), patch.object(runner, 'TOP_N', 2), \
                patch.object(runner, 'DOWNLOAD_TOP', '1'), \
                patch.object(runner, 'sample_download', return_value=probe_result()) as probe:
            selected, counts = runner.sample_candidates(rows, '4', 'example.com', '/')
        self.assertEqual(probe.call_count, 4)
        self.assertEqual(counts['download_success_count'], 4)
        self.assertEqual(len(selected), 2)

    def test_real_pipe_flood_deadline_and_secret_safe_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            source = ('import os,time\n'
                      'os.write(1,b"secret-token\\n" + b"x"*1000000 + b"\\n")\n'
                      'os.write(2,b\'{"ip":"192.0.2.1","ok":true,"score_ms":1}\\n\')\n'
                      'time.sleep(10)\n')
            start = time.monotonic()
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                result = runner.run_mcis([sys.executable, '-c', source], path, '4', .35)
            self.assertLess(time.monotonic() - start, 2)
            self.assertTrue(result['scan_timed_out'])
            self.assertGreater(path.stat().st_size, 1000000)
            self.assertNotIn('secret-token', capture.getvalue())
            self.assertIn('MCIS progress', capture.getvalue())
            self.assertEqual(len(runner.parse_json_lines(path)[1]), 1)

    def test_live_progress_exposes_numeric_scan_count_not_child_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = 'import os,time;os.write(1,b"progress: 12/3000 done, token=private\\n");time.sleep(.2)'
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                runner.run_mcis([sys.executable, '-c', source], Path(tmp) / 'scan', '6', 2)
            self.assertIn('12/3000', capture.getvalue())
            self.assertNotIn('private', capture.getvalue())

    def test_mcis_inherits_process_group_and_nonzero_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            runner.run_mcis([sys.executable, '-c', 'import os;print(os.getpgrp())'], path, '4', 1)
            self.assertEqual(int(path.read_text()), os.getpgrp())
            with self.assertRaises(RuntimeError):
                runner.run_mcis([sys.executable, '-c', 'raise SystemExit(3)'], path, '4', 1)

    def test_attempt_cap_target_and_effective_timeout(self):
        rows = [dict(ip=f'192.0.2.{i}', ok=True, latency_ms=10, score_ms=i) for i in range(1, 101)]
        for configured, success, expected in [('999', False, 50), ('1', False, 6), ('20', True, 3), ('0', True, 0)]:
            with self.subTest(configured=configured, success=success), \
                    patch.object(runner, 'DOWNLOAD_TOP', configured), patch.object(runner, 'TOP_N', 3), \
                    patch.object(runner, 'DOWNLOAD_TIMEOUT', '90s'), \
                    patch.object(runner, 'sample_download', return_value=probe_result(success)) as probe:
                selected, counts = runner.sample_candidates(rows, '4', 'example.com', '/')
                self.assertEqual(probe.call_count, expected)
                self.assertEqual(counts['download_attempt_count'], expected)
                self.assertLessEqual(len(selected), 3)
                for call in probe.call_args_list:
                    self.assertLessEqual(call.kwargs['timeout_seconds'], 15)
                if success and expected:
                    self.assertIsInstance(selected[0]['download_measured_at'], float)
                    self.assertEqual(selected[0]['download_method'], 'windowed_http')
                if not expected:
                    self.assertNotIn('download_measured_at', selected[0])
                    self.assertEqual(selected[0]['download_stop_reason'], 'disabled')

    def test_min_speed_and_loss_filters_require_fully_qualified_target(self):
        rows = [dict(ip=f'192.0.2.{i}', ok=True, latency_ms=10, score_ms=i) for i in range(1, 8)]
        def check(rows, *args):
            row = rows[0]
            row['loss_rate'] = 1 if row['ip'].endswith('.1') else 0
            return rows
        with patch.object(runner, 'TOP_N', 2), patch.object(runner, 'MIN_DOWNLOAD_MBPS', 10), \
                patch.object(runner, 'MAX_LOSS_RATE', .1), \
                patch.object(runner, 'add_per_ip_loss_rechecks', side_effect=check), \
                patch.object(runner, 'sample_download', side_effect=[probe_result(True, 20), probe_result(True, 1), probe_result(True, 20), probe_result(True, 30)]) as probe:
            selected, counts = runner.sample_candidates(rows, '4', 'example.com', '/')
        self.assertEqual(probe.call_count, 4)
        self.assertEqual(counts['download_qualified_count'], 2)
        self.assertEqual({r['ip'] for r in selected}, {'192.0.2.3', '192.0.2.4'})

    def test_budget_retains_samples_and_labels_unsampled_rejects_min_speed(self):
        rows = [dict(ip=f'192.0.2.{i}', ok=True, score_ms=i) for i in range(1, 4)]
        def slow(*args, **kwargs):
            time.sleep(.04)
            return probe_result()
        for minimum in (0, 1):
            with patch.object(runner, 'DOWNLOAD_BUDGET', '20ms'), patch.object(runner, 'TOP_N', 2), \
                    patch.object(runner, 'MIN_DOWNLOAD_MBPS', minimum), \
                    patch.object(runner, 'sample_download', side_effect=slow):
                selected, counts = runner.sample_candidates(rows, '4', 'example.com', '/')
            self.assertTrue(counts['download_budget_expired'])
            self.assertEqual(counts['download_attempt_count'], 1)
            self.assertEqual(len(selected), 2 if not minimum else 1)
            if not minimum:
                self.assertEqual(selected[1]['download_stop_reason'], 'budget')
                self.assertNotIn('download_mbps', selected[1])

    def test_zone_success_cache_is_process_local_token_scoped_manual_bypasses(self):
        runner._ZONE_CACHE.clear()
        response = {'result': [{'id': 'zone', 'name': 'example.com'}]}
        try:
            with patch.object(runner, 'cf_request', return_value=response) as api:
                self.assertEqual(runner.resolve_zone_id('a', 'example.com'), 'zone')
                self.assertEqual(runner.resolve_zone_id('a', 'example.com'), 'zone')
                self.assertEqual(api.call_count, 1)
                self.assertEqual(runner.resolve_zone_id('b', 'example.com'), 'zone')
                self.assertEqual(api.call_count, 2)
                self.assertEqual(runner.resolve_zone_id('a', 'example.com', 'manual'), 'manual')
                self.assertEqual(api.call_count, 2)
        finally:
            runner._ZONE_CACHE.clear()
