# SPDX-License-Identifier: GPL-3.0-only
"""Grab keyboard events only while the compositor reports a dark display."""
import fcntl
import logging
import threading

DESTINATION = 'com.lomiri.SystemCompositor.Display'
OBJECT = '/com/lomiri/SystemCompositor/Display'
PROPERTIES = 'org.freedesktop.DBus.Properties'
EVIOCGRAB = 0x40044590
LOG = logging.getLogger('pro1x-slider')


class KeyboardGate:
    """Own the grab, not the descriptor; release before its owner closes it.

    A dedicated GLib context handles display changes immediately, including
    while the main watcher is waiting on GSettings. Loss of trustworthy display
    state releases the grab so ordinary typing remains available.
    """
    def __init__(self, fd):
        self.fd = fd
        self.lock = threading.Lock()
        self.grabbed = False
        self.closed = False
        self.error = 'Waiting for display state'
        self.ready = threading.Event()
        self.context = None
        self.thread = threading.Thread(target=self.run, name='keyboard-display-gate', daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            error = self.error or 'Display monitor did not start'
            self.close()
            raise RuntimeError(error)

    def update(self, outputs=None, error=None):
        with self.lock:
            if self.closed:
                return
            dark = False
            if outputs is not None:
                try:
                    if len(outputs) != 2 or any(type(value) is not int or value < 0 for value in outputs):
                        raise ValueError('Unexpected display state')
                    dark = outputs[0] == 0 and outputs[1] == 0
                except (TypeError, ValueError) as failure:
                    error = str(failure)
            try:
                wanted = dark and error is None
                if wanted != self.grabbed:
                    fcntl.ioctl(self.fd, EVIOCGRAB, int(wanted))
                    self.grabbed = wanted
                self.error = error
            except OSError as failure:
                self.error = str(failure)

    def run(self):
        bus, subscriptions, timer, closed_handler = None, [], None, None
        try:
            from gi.repository import Gio, GLib
            self.context = GLib.MainContext.new()
            self.context.push_thread_default()
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

                def refresh(*_):
                    if self.closed:
                        return False
                    try:
                        outputs = bus.call_sync(DESTINATION, OBJECT, PROPERTIES, 'Get',
                            GLib.Variant('(ss)', (DESTINATION, 'ActiveOutputs')), None,
                            Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
                        self.update(outputs)
                    except Exception as failure:
                        self.update(error='Display monitor: ' + str(failure))
                    return not self.closed

                def changed(_connection, _sender, _path, _interface, _signal, parameters):
                    interface, values, invalidated = parameters.unpack()
                    if interface != DESTINATION:
                        return
                    if 'ActiveOutputs' in values:
                        self.update(values['ActiveOutputs'])
                    elif 'ActiveOutputs' in invalidated:
                        refresh()

                def owner_changed(_connection, _sender, _path, _interface, _signal, parameters):
                    _, _, owner = parameters.unpack()
                    if owner:
                        refresh()
                    else:
                        self.update(error='Display service unavailable')

                subscriptions.append(bus.signal_subscribe(DESTINATION, PROPERTIES, 'PropertiesChanged',
                    OBJECT, None, Gio.DBusSignalFlags.NONE, changed))
                subscriptions.append(bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
                    'NameOwnerChanged', '/org/freedesktop/DBus', DESTINATION,
                    Gio.DBusSignalFlags.NONE, owner_changed))
                closed_handler = bus.connect('closed', lambda *_: self.update(error='System bus disconnected'))
                refresh()
                self.ready.set()
                # Recovery only; normal on/off transitions use PropertiesChanged.
                timer = GLib.timeout_source_new_seconds(2)
                timer.set_callback(refresh)
                timer.attach(self.context)
                while not self.closed:
                    self.context.iteration(True)
            finally:
                if timer is not None:
                    timer.destroy()
                if bus is not None:
                    for subscription in subscriptions:
                        bus.signal_unsubscribe(subscription)
                    if closed_handler is not None:
                        bus.disconnect(closed_handler)
                self.context.pop_thread_default()
        except Exception as failure:
            self.update(error='Display monitor: ' + str(failure))
        finally:
            self.ready.set()

    def close(self):
        with self.lock:
            self.closed = True
            if self.grabbed:
                try:
                    fcntl.ioctl(self.fd, EVIOCGRAB, 0)
                except OSError:
                    # The owner closes the descriptor after close(), releasing
                    # any remaining grab even if the explicit ioctl failed.
                    LOG.exception('Could not explicitly release keyboard grab')
                finally:
                    self.grabbed = False
        if self.context is not None:
            self.context.wakeup()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=3)
