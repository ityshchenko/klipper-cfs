import sys
import types

import pytest

serial_stub = types.ModuleType('serial')
serial_stub.Serial = object
serial_stub.SerialException = Exception
sys.modules.setdefault('serial', serial_stub)

from extras.creality_cfs import CfsConnection, _crc8, _to_hex


class DummyReactor:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class DummyGcode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, fn, desc=None):
        self.commands[name] = (fn, desc)


class DummyPrinter:
    def __init__(self):
        self.reactor = DummyReactor()
        self.gcode = DummyGcode()

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name):
        assert name == 'gcode'
        return self.gcode

    def register_event_handler(self, _event, _handler):
        pass

    def command_error(self, msg):
        return RuntimeError(msg)

    def config_error(self, msg):
        return ValueError(msg)


class DummyConfig:
    def __init__(self):
        self.printer = DummyPrinter()

    def get_printer(self):
        return self.printer

    def get_name(self):
        return 'cfs test'

    def get(self, key, default=None):
        values = {'serial': '/dev/null'}
        return values.get(key, default)

    def getint(self, _key, default=None, minval=None, maxval=None):
        del minval, maxval
        return default

    def getfloat(self, _key, default=None, above=None):
        del above
        return default

    def getboolean(self, _key, default=None):
        return default

    def error(self, msg):
        return RuntimeError(msg)


class DummyCmd:
    def __init__(self, values=None):
        self.values = values or {}
        self.messages = []

    def get(self, name, default=None):
        return self.values.get(name, default)

    def get_int(self, name, default=None, minval=None, maxval=None):
        val = self.values.get(name, default)
        if val is None:
            raise ValueError('missing %s' % (name,))
        if minval is not None and val < minval:
            raise ValueError('below min')
        if maxval is not None and val > maxval:
            raise ValueError('above max')
        return val

    def respond_info(self, msg):
        self.messages.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


@pytest.fixture
def conn():
    return CfsConnection(DummyConfig())


def test_to_hex_and_crc8_basics():
    assert _to_hex(b'\x00\xAF') == '00AF'
    assert _crc8(bytes([0x03, 0xFF, 0x0A])) == 0x5C


def test_build_and_parse_frame_roundtrip(conn):
    frame = conn._build_frame(addr=0x01, status=0xFF, fn=0x04, data=b'\x00\x01')
    parsed = conn._parse_frame(frame)
    assert parsed['addr'] == 0x01
    assert parsed['status'] == 0xFF
    assert parsed['fn'] == 0x04
    assert parsed['data'] == b'\x00\x01'
    assert parsed['raw'] == frame


def test_parse_frame_rejects_bad_header(conn):
    with pytest.raises(RuntimeError, match='invalid cfs frame'):
        conn._parse_frame(b'\x00\x01\x03\x00\xA3\xDD')


def test_parse_frame_rejects_bad_crc(conn):
    good = conn._build_frame(addr=0x01, status=0x00, fn=0xA3)
    bad = good[:-1] + bytes([good[-1] ^ 0xFF])
    with pytest.raises(RuntimeError, match='crc mismatch'):
        conn._parse_frame(bad)


def test_query_frame_timeout_raises(conn):
    class SerialStub:
        def write(self, _data):
            pass

        def flush(self):
            pass

    conn._serial = SerialStub()
    conn._wait_for_rx = lambda _deadline: None
    with pytest.raises(RuntimeError, match='cfs response timeout'):
        conn._query_frame(0x01, 0x04, b'\x00', wait=True)


def test_cmd_rfid_write_rejects_empty_data(conn):
    conn._connected = True
    conn._serial = object()
    gcmd = DummyCmd({'SPOOL': 1, 'DATA_HEX': ''})
    with pytest.raises(RuntimeError, match='DATA_HEX must not be empty'):
        conn.cmd_CFS_RFID_WRITE(gcmd)


def test_cmd_get_active_spool_formats_response(conn):
    conn._connected = True
    conn._serial = object()
    conn._query_frame = lambda _addr, _fn, _data, wait=True: {
        'tx': b'\xF7',
        'rx': b'\xF7\x01\x04\x00\x0A\x02\xA9',
        'parsed': {'data': b'\x02'},
    }
    gcmd = DummyCmd({'ADDR': 1})
    conn.cmd_CFS_GET_ACTIVE_SPOOL(gcmd)
    assert gcmd.messages
    assert 'active_spool=3' in gcmd.messages[0]
