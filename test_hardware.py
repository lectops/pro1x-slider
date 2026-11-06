# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import hardware
import keyboard_gate
from slider_common import default_config


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.device = hardware.Hardware()
        self.config = default_config()
        self.wake = Mock()
        self.wake.wake.return_value = False
        self.gate = Mock(error=None)
        for target, value in [('WakeOutput', self.wake), ('open_input', 99),
                              ('keyboard_wake_supported', True), ('KeyboardGate', self.gate)]:
            patcher = patch.object(hardware, target, return_value=value)
            self.addCleanup(patcher.stop)
            setattr(self, target, patcher.start())
        for name in ['fcntl.ioctl', 'os.close']:
            patcher = patch('hardware.' + name)
            self.addCleanup(patcher.stop)
            setattr(self, name.split('.')[-1], patcher.start())

    def test_slider_and_key_wake_only_on_requested_physical_events(self):
        self.config['wake'] = {'slider_open': True, 'keyboard': True}
        self.device.reconcile(self.config, True)
        self.wake.wake.assert_not_called()  # Starting/configuring is not opening.
        for event in [('slider', 5, 10, 1, False), ('keyboard', 1, 30, 1, False),
                      ('keyboard', 1, 30, 2, True), ('keyboard', 1, 30, 0, True),
                      ('keyboard', 0, 3, 0, True), ('fingerprint', 1, 116, 1, True)]:
            self.device.event(*event)
        self.wake.wake.assert_not_called()
        self.device.event('slider', 5, 10, 0, True)
        self.device.event('keyboard', 1, 30, 1, True)
        self.assertEqual(self.wake.wake.call_count, 2)
        self.config['enabled'] = False
        self.device.reconcile(self.config, True)
        self.device.event('keyboard', 1, 30, 1, True)
        self.assertEqual(self.wake.wake.call_count, 2)
        self.wake.close.assert_called_once()

    def test_missing_kernel_does_not_open_keyboard_or_block_slider_wake(self):
        self.keyboard_wake_supported.return_value = False
        self.config['wake'] = {'slider_open': True, 'keyboard': True}
        self.device.reconcile(self.config, True)
        self.open_input.assert_not_called()
        self.assertEqual(self.device.features['keyboard_wake']['state'], 'unsupported')
        self.assertEqual(self.device.features['slider_wake']['state'], 'active')
        self.device.event('slider', 5, 10, 0, True)
        self.wake.wake.assert_called_once()

    def test_keyboard_off_gates_input_and_on_releases_before_reopening(self):
        self.device.reconcile(self.config, True)
        self.KeyboardGate.assert_called_once_with(99)
        self.assertEqual(self.device.features['keyboard_wake']['state'], 'disabled')
        self.device.event('keyboard', 1, 30, 1, True)
        self.wake.wake.assert_not_called()
        order = Mock()
        order.attach_mock(self.gate.close, 'gate_close')
        self.close.side_effect = order.fd_close
        self.config['wake']['keyboard'] = True
        self.device.reconcile(self.config, True)
        self.assertEqual([call[0] for call in order.mock_calls], ['gate_close', 'fd_close'])
        self.assertIsNone(self.device.gate)
        self.device.event('keyboard', 1, 30, 1, True)
        self.wake.wake.assert_called_once()

    def test_pause_and_stop_release_keyboard_gate(self):
        self.device.reconcile(self.config, True)
        self.config['enabled'] = False
        self.device.reconcile(self.config, True)
        self.gate.close.assert_called_once()
        self.assertIsNone(self.device.keyboard)
        self.config['enabled'] = True
        self.device.reconcile(self.config, True)
        self.device.close()
        self.assertEqual(self.gate.close.call_count, 2)

    def test_display_monitor_failure_is_visible_and_toggle_can_recover(self):
        self.gate.error = 'Display service unavailable'
        self.device.reconcile(self.config, True)
        self.assertEqual(self.device.features['keyboard_wake']['state'], 'error')
        self.assertTrue(self.device.capabilities['keyboard_wake'])
        self.config['wake']['keyboard'] = True
        self.device.reconcile(self.config, True)
        self.assertEqual(self.device.features['keyboard_wake']['state'], 'active')
        self.gate.close.assert_called_once()

    def test_fingerprint_filter_releases_on_toggle_pause_and_shutdown(self):
        self.keyboard_wake_supported.return_value = False
        self.config['fingerprint']['wake_screen'] = False
        self.device.reconcile(self.config, False)
        self.device.reconcile(self.config, True)
        self.open_input.assert_called_once_with('madev', fingerprint=True)
        self.ioctl.assert_called_once_with(99, hardware.EVIOCGRAB, 1)
        self.WakeOutput.assert_not_called()
        self.config['enabled'] = False
        self.device.reconcile(self.config, None)
        self.close.assert_called_once_with(99)
        self.assertFalse(self.config['fingerprint']['wake_screen'])
        self.config['enabled'] = True
        self.device.reconcile(self.config, None)
        self.device.close()
        self.assertEqual(self.close.call_count, 2)
        self.assertIsNone(self.device.fingerprint)
        self.config['fingerprint']['wake_screen'] = True
        self.device.reconcile(self.config, True)
        self.assertIsNone(self.device.fingerprint)

    def test_busy_fingerprint_input_is_reported_without_stopping_wake(self):
        self.keyboard_wake_supported.return_value = False
        self.config['wake']['slider_open'] = True
        self.config['fingerprint']['wake_screen'] = False
        self.ioctl.side_effect = OSError('Device or resource busy')
        self.device.reconcile(self.config, True)
        self.assertIsNone(self.device.fingerprint)
        self.close.assert_called_once_with(99)
        self.assertEqual(self.device.features['fingerprint']['state'], 'error')
        self.assertEqual(self.device.features['slider_wake']['state'], 'active')

    def test_display_query_failure_does_not_send_a_blind_wake(self):
        self.config['wake']['slider_open'] = True
        self.device.reconcile(self.config, True)
        self.wake.wake.side_effect = RuntimeError('Compositor unavailable')
        self.device.event('slider', 5, 10, 0, True)
        self.assertEqual(self.device.features['slider_wake']['state'], 'error')
        self.wake.close.assert_called_once()
        self.assertIsNone(self.device.output)

    def test_fingerprint_events_are_consumed_without_forwarding(self):
        self.keyboard_wake_supported.return_value = False
        self.config['fingerprint']['wake_screen'] = False
        self.device.reconcile(self.config, False)
        data = hardware.EVENT.pack(0, 0, 1, 116, 1)
        with patch('hardware.os.read', return_value=data):
            self.device.consume(99, False)
        self.wake.wake.assert_not_called()
        with patch('hardware.os.read', side_effect=OSError('Disconnected')):
            self.device.consume(99, False)
        self.assertIsNone(self.device.fingerprint)
        self.assertEqual(self.device.features['fingerprint']['state'], 'error')


class WakeOutputTests(unittest.TestCase):
    def test_screen_on_does_nothing_and_screen_off_emits_wake_not_power(self):
        output = hardware.WakeOutput.__new__(hardware.WakeOutput)
        output.fd, output.ready_at, output.last_request = 10, 0, -100
        output.screen_off = Mock(return_value=False)
        with patch('hardware.time.monotonic', return_value=10), patch('hardware.os.write') as write:
            self.assertFalse(output.wake())
            write.assert_not_called()
        output.screen_off.return_value = True
        with patch('hardware.time.monotonic', return_value=11), patch('hardware.os.write') as write:
            self.assertTrue(output.wake())
            events = [hardware.EVENT.unpack(call.args[1])[2:] for call in write.call_args_list]
            self.assertEqual(events, [(1, 143, 1), (0, 0, 0), (1, 143, 0), (0, 0, 0)])
            self.assertFalse(output.wake())  # Repeated wake is rate limited.


class InputGuardTests(unittest.TestCase):
    def test_keyboard_identity_must_match_before_monitor_can_grab(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'event3/device').mkdir(parents=True)
            (root / 'event3/device/name').write_text('Fxtec Pro1\n')
            with patch.object(hardware, 'INPUT_CLASS', root), \
                    patch('hardware.os.open', return_value=99), \
                    patch('hardware.os.close') as close:
                for identity in [(0x18, 0x9523, 0x701, 1), (3, 0x9523, 0x701, 1)]:
                    with patch.object(hardware, 'ioctl_read', side_effect=[
                            b'Fxtec Pro1\0', struct.pack('HHHH', *identity)]):
                        if identity[0] == 0x18:
                            self.assertEqual(hardware.open_input('Fxtec Pro1'), 99)
                            close.assert_not_called()
                        else:
                            with self.assertRaisesRegex(RuntimeError, 'Unsupported hardware keyboard'):
                                hardware.open_input('Fxtec Pro1')
                            close.assert_called_once_with(99)

    def test_changed_identity_or_extra_keys_are_rejected_before_any_grab(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'class/event8/device').mkdir(parents=True)
            (root / 'class/event8/device/name').write_text('madev\n')
            (root / 'dev').mkdir()
            (root / 'dev/event8').write_bytes(b'')
            keys = sum(1 << key for key in hardware.FINGERPRINT_KEYS)
            variants = [((0x1c, 0, 0, 0), keys, True),
                        ((3, 0, 0, 0), keys, False),
                        ((0x1c, 0, 0, 0), keys | (1 << 30), False)]
            for identity, bits, accepted in variants:
                with self.subTest(identity=identity, keys=bits), \
                        patch.object(hardware, 'INPUT_CLASS', root / 'class'), \
                        patch.object(hardware, 'INPUT_DEVICES', root / 'dev'), \
                        patch.object(hardware, 'ioctl_read', side_effect=[
                            b'madev\0', struct.pack('HHHH', *identity), bits.to_bytes(96, 'little')]), \
                        patch('hardware.fcntl.ioctl') as grab:
                    if accepted:
                        fd = hardware.open_input('madev', fingerprint=True)
                        hardware.os.close(fd)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Unsupported fingerprint'):
                            hardware.open_input('madev', fingerprint=True)
                    grab.assert_not_called()


class KeyboardGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = keyboard_gate.KeyboardGate.__new__(keyboard_gate.KeyboardGate)
        self.gate.fd = 99
        self.gate.lock = threading.Lock()
        self.gate.grabbed = self.gate.closed = False
        self.gate.error = None
        self.gate.context = Mock()
        self.gate.thread = Mock()
        patcher = patch('keyboard_gate.fcntl.ioctl')
        self.ioctl = patcher.start()
        self.addCleanup(patcher.stop)

    def test_dark_display_is_grabbed_until_any_output_is_on(self):
        for outputs in [(1, 0), (0, 1)]:
            self.gate.update((0, 0))
            self.assertTrue(self.gate.grabbed)
            self.gate.update(outputs)
            self.assertFalse(self.gate.grabbed)
        self.assertEqual([call.args[2] for call in self.ioctl.call_args_list], [1, 0, 1, 0])

    def test_unknown_or_lost_display_state_releases_for_typing(self):
        for outputs in [(-1, 0), (0,), (False, 0), 'invalid', 0]:
            self.gate.update((0, 0))
            self.gate.update(outputs)
            self.assertFalse(self.gate.grabbed)
            self.assertIsNotNone(self.gate.error)
        self.gate.update((0, 0))
        self.gate.update(error='Display service unavailable')
        self.assertFalse(self.gate.grabbed)
        self.assertEqual(self.gate.error, 'Display service unavailable')
        self.gate.update((0, 0))
        self.assertTrue(self.gate.grabbed)
        self.assertIsNone(self.gate.error)

    def test_failed_release_is_reported_and_retried(self):
        self.gate.update((0, 0))
        self.ioctl.side_effect = OSError('Interrupted')
        self.gate.update((1, 0))
        self.assertTrue(self.gate.grabbed)
        self.assertIn('Interrupted', self.gate.error)
        self.ioctl.side_effect = None
        self.gate.update((1, 0))
        self.assertFalse(self.gate.grabbed)
        self.assertIsNone(self.gate.error)

    def test_close_releases_and_prevents_late_callback_grab(self):
        self.gate.update((0, 0))
        self.gate.close()
        self.gate.update((0, 0))
        self.assertEqual([call.args[2] for call in self.ioctl.call_args_list], [1, 0])
        self.gate.context.wakeup.assert_called_once()
        self.gate.thread.join.assert_called_once_with(timeout=3)


if __name__ == '__main__':
    unittest.main()
