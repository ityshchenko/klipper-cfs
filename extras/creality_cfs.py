"""Creality CFS RS485 host link.

This module intentionally implements only RS485 protocol operations for CFS.
It does not invoke nozzle-cleaning, movement, or heater actions.
"""

import binascii
import logging
import threading

import serial
from serial import SerialException

try:
    from queue import Queue, Empty
except ImportError:
    from Queue import Queue, Empty


def _to_hex(data):
    return binascii.hexlify(data).decode('ascii').upper()


def _crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07 if crc & 0x80 else crc << 1) & 0xFF
    return crc


class CfsConnection:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name().split()[-1]

        self.serial_port = config.get('serial')
        if not self.serial_port:
            raise config.error('`serial` is required for [cfs]')
        self.baud = config.getint('baud', default=230400, minval=1200)
        self.timeout = config.getfloat('timeout', default=0.25, above=0.)
        self.connect_timeout = config.getfloat(
            'connect_timeout', default=3.0, above=0.)
        self.retry_count = config.getint('retry_count', default=3, minval=1)

        self.handshake_hex = config.get('handshake_hex', default='')
        self.handshake_expect_hex = config.get('handshake_expect_hex',
                                               default='')
        self.auto_connect = config.getboolean('auto_connect', default=True)
        self.default_addr = config.getint('default_addr', default=0x01,
                                          minval=0x01, maxval=0xFF)
        self.rfid_read_fn = config.getint('rfid_read_fn', default=0x02,
                                          minval=0, maxval=0xFF)
        self.rfid_write_fn = config.getint('rfid_write_fn', default=0x02,
                                           minval=0, maxval=0xFF)
        self.rfid_write_op = config.getint('rfid_write_op', default=0x01,
                                           minval=0, maxval=0xFF)

        self._serial = None
        self._rx_queue = Queue()
        self._rx_stop = threading.Event()
        self._rx_thread = None
        self._connected = False
        self._last_rx = None
        self._last_error = None

        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler('klippy:disconnect',
                                            self._disconnect)

        self.gcode.register_command('CFS_CONNECT', self.cmd_CFS_CONNECT,
                                    desc=self.cmd_CFS_CONNECT_help)
        self.gcode.register_command('CFS_DISCONNECT', self.cmd_CFS_DISCONNECT,
                                    desc=self.cmd_CFS_DISCONNECT_help)
        self.gcode.register_command('CFS_STATUS', self.cmd_CFS_STATUS,
                                    desc=self.cmd_CFS_STATUS_help)
        self.gcode.register_command('CFS_SEND_HEX', self.cmd_CFS_SEND_HEX,
                                    desc=self.cmd_CFS_SEND_HEX_help)
        self.gcode.register_command('CFS_QUERY', self.cmd_CFS_QUERY,
                                    desc=self.cmd_CFS_QUERY_help)
        self.gcode.register_command('CFS_SELECT_SPOOL',
                                    self.cmd_CFS_SELECT_SPOOL,
                                    desc=self.cmd_CFS_SELECT_SPOOL_help)
        self.gcode.register_command('CFS_GET_ACTIVE_SPOOL',
                                    self.cmd_CFS_GET_ACTIVE_SPOOL,
                                    desc=self.cmd_CFS_GET_ACTIVE_SPOOL_help)
        self.gcode.register_command('CFS_FEED_SPOOL', self.cmd_CFS_FEED_SPOOL,
                                    desc=self.cmd_CFS_FEED_SPOOL_help)
        self.gcode.register_command('CFS_REWIND_SPOOL',
                                    self.cmd_CFS_REWIND_SPOOL,
                                    desc=self.cmd_CFS_REWIND_SPOOL_help)
        self.gcode.register_command('CFS_RUNOUT_STATUS',
                                    self.cmd_CFS_RUNOUT_STATUS,
                                    desc=self.cmd_CFS_RUNOUT_STATUS_help)
        self.gcode.register_command('CFS_RFID_READ',
                                    self.cmd_CFS_RFID_READ,
                                    desc=self.cmd_CFS_RFID_READ_help)
        self.gcode.register_command('CFS_RFID_WRITE',
                                    self.cmd_CFS_RFID_WRITE,
                                    desc=self.cmd_CFS_RFID_WRITE_help)

    def _parse_hex(self, hex_str, field_name):
        if not hex_str:
            return b''
        try:
            clean_hex = ''.join(hex_str.split())
            return bytes.fromhex(clean_hex)
        except ValueError:
            raise self.printer.config_error(
                "invalid %s value '%s' (must be hex bytes)" % (
                    field_name, hex_str))

    def _rx_loop(self):
        while not self._rx_stop.is_set():
            try:
                if self._serial is None:
                    break
                data = self._serial.read(256)
            except Exception as e:
                self._last_error = str(e)
                logging.exception('cfs: serial read failure')
                break
            if data:
                self._last_rx = data
                self._rx_queue.put((self.reactor.monotonic(), data))

    def _clear_rx_queue(self):
        while True:
            try:
                self._rx_queue.get_nowait()
            except Empty:
                return

    def _wait_for_rx(self, deadline):
        while self.reactor.monotonic() < deadline:
            timeout = max(0.01, min(0.1, deadline - self.reactor.monotonic()))
            try:
                _, data = self._rx_queue.get(timeout=timeout)
                return data
            except Empty:
                pass
        return None

    def _require_connected(self, gcmd):
        if not self._connected or self._serial is None:
            raise gcmd.error('cfs is not connected')

    def _build_frame(self, addr, status, fn, data=b''):
        payload = bytes(data)
        length = 3 + len(payload)
        body = bytes([length & 0xFF, status & 0xFF, fn & 0xFF]) + payload
        crc = _crc8(body)
        return bytes([0xF7, addr & 0xFF]) + body + bytes([crc])

    def _parse_frame(self, data):
        if not data or len(data) < 6 or data[0] != 0xF7:
            raise self.printer.command_error('invalid cfs frame')
        length = data[2]
        total = length + 3
        if len(data) < total:
            raise self.printer.command_error('short cfs frame')
        frame = data[:total]
        check = _crc8(frame[2:-1])
        if check != frame[-1]:
            raise self.printer.command_error(
                'crc mismatch: %02X != %02X' % (check, frame[-1]))
        return {
            'raw': frame,
            'addr': frame[1],
            'length': frame[2],
            'status': frame[3],
            'fn': frame[4],
            'data': frame[5:-1],
            'crc': frame[-1],
        }

    def _query_frame(self, addr, fn, data=b'', wait=True):
        self._clear_rx_queue()
        tx = self._build_frame(addr, 0xFF, fn, data)
        self._serial.write(tx)
        self._serial.flush()
        if not wait:
            return {'tx': tx, 'rx': None, 'parsed': None}
        deadline = self.reactor.monotonic() + self.connect_timeout
        rx = self._wait_for_rx(deadline)
        if rx is None:
            raise self.printer.command_error('cfs response timeout')
        parsed = self._parse_frame(rx)
        return {'tx': tx, 'rx': rx, 'parsed': parsed}

    def _gcmd_addr(self, gcmd):
        return gcmd.get_int('ADDR', self.default_addr, minval=0x01,
                            maxval=0xFF)

    def _connect(self):
        if not self.auto_connect or self._connected:
            return
        self._do_connect()

    def _do_connect(self):
        last_exception = None
        for _ in range(self.retry_count):
            try:
                self._clear_rx_queue()
                self._last_error = None
                self._serial = serial.Serial(self.serial_port, self.baud,
                                             timeout=self.timeout,
                                             write_timeout=self.timeout)
                self._rx_stop.clear()
                self._rx_thread = threading.Thread(target=self._rx_loop)
                self._rx_thread.daemon = True
                self._rx_thread.start()
                self._perform_handshake()
                self._connected = True
                logging.info('cfs: connected to %s @ %d',
                             self.serial_port, self.baud)
                return
            except Exception as e:
                last_exception = e
                self._disconnect()
        raise self.printer.command_error(
            'Unable to connect CFS on %s: %s' % (self.serial_port,
                                                 last_exception))

    def _perform_handshake(self):
        tx = self._parse_hex(self.handshake_hex, 'handshake_hex')
        expect = self._parse_hex(self.handshake_expect_hex,
                                 'handshake_expect_hex')
        if not tx:
            return
        self._serial.write(tx)
        self._serial.flush()
        deadline = self.reactor.monotonic() + self.connect_timeout
        rx = self._wait_for_rx(deadline)
        if rx is None:
            raise self.printer.command_error(
                'cfs: handshake timeout (no response)')
        if expect and expect not in rx:
            raise self.printer.command_error(
                'cfs: unexpected handshake response: %s' % (_to_hex(rx),))

    def _disconnect(self):
        self._connected = False
        self._rx_stop.set()
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=0.5)
            self._rx_thread = None
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    cmd_CFS_CONNECT_help = 'Open CFS RS485 link and run optional handshake.'
    def cmd_CFS_CONNECT(self, gcmd):
        del gcmd
        if self._connected:
            return
        self._do_connect()

    cmd_CFS_DISCONNECT_help = 'Close CFS RS485 link.'
    def cmd_CFS_DISCONNECT(self, gcmd):
        del gcmd
        self._disconnect()

    cmd_CFS_STATUS_help = 'Report CFS link state.'
    def cmd_CFS_STATUS(self, gcmd):
        status = {
            'name': self.name,
            'connected': self._connected,
            'serial': self.serial_port,
            'baud': self.baud,
            'last_rx': _to_hex(self._last_rx) if self._last_rx else '',
            'last_error': self._last_error or '',
        }
        gcmd.respond_info('cfs: %s' % (status,))

    cmd_CFS_SEND_HEX_help = 'Send raw hex payload to CFS; optional WAIT=1 waits for one response frame.'
    def cmd_CFS_SEND_HEX(self, gcmd):
        self._require_connected(gcmd)
        payload_hex = gcmd.get('HEX')
        payload = self._parse_hex(payload_hex, 'HEX')
        wait = gcmd.get_int('WAIT', 0, minval=0, maxval=1)
        self._serial.write(payload)
        self._serial.flush()
        if not wait:
            return
        deadline = self.reactor.monotonic() + self.connect_timeout
        rx = self._wait_for_rx(deadline)
        if rx is None:
            raise gcmd.error('cfs response timeout')
        gcmd.respond_info('cfs_rx=%s' % (_to_hex(rx),))

    cmd_CFS_QUERY_help = (
        'Send CFS protocol frame. Params: FN=<0-255> [ADDR=<1-255>] '
        '[STATUS=<0-255>] [DATA_HEX=<hex>] [WAIT=1].')
    def cmd_CFS_QUERY(self, gcmd):
        self._require_connected(gcmd)
        fn = gcmd.get_int('FN', minval=0, maxval=0xFF)
        addr = self._gcmd_addr(gcmd)
        status = gcmd.get_int('STATUS', 0xFF, minval=0, maxval=0xFF)
        data = self._parse_hex(gcmd.get('DATA_HEX', ''), 'DATA_HEX')
        wait = gcmd.get_int('WAIT', 1, minval=0, maxval=1)
        tx = self._build_frame(addr, status, fn, data)
        self._clear_rx_queue()
        self._serial.write(tx)
        self._serial.flush()
        gcmd.respond_info('cfs_tx=%s' % (_to_hex(tx),))
        if not wait:
            return
        deadline = self.reactor.monotonic() + self.connect_timeout
        rx = self._wait_for_rx(deadline)
        if rx is None:
            raise gcmd.error('cfs response timeout')
        parsed = self._parse_frame(rx)
        gcmd.respond_info('cfs_rx=%s parsed=%s' % (_to_hex(rx), parsed))

    cmd_CFS_SELECT_SPOOL_help = (
        'Select spool. Params: SPOOL=<1-4> [ADDR=<1-255>] [MODE=<0-255>]')
    def cmd_CFS_SELECT_SPOOL(self, gcmd):
        self._require_connected(gcmd)
        spool = gcmd.get_int('SPOOL', minval=1, maxval=4)
        addr = self._gcmd_addr(gcmd)
        mode = gcmd.get_int('MODE', 1, minval=0, maxval=0xFF)
        payload = bytes([spool - 1, mode])
        result = self._query_frame(addr, 0x04, payload, wait=True)
        gcmd.respond_info('cfs_select_spool tx=%s rx=%s' % (
            _to_hex(result['tx']), _to_hex(result['rx'])))

    cmd_CFS_GET_ACTIVE_SPOOL_help = 'Query box state and report possible active spool byte.'
    def cmd_CFS_GET_ACTIVE_SPOOL(self, gcmd):
        self._require_connected(gcmd)
        addr = self._gcmd_addr(gcmd)
        result = self._query_frame(addr, 0x0A, b'', wait=True)
        pdata = result['parsed']['data']
        active_raw = pdata[0] if pdata else None
        active_spool = (active_raw + 1) if active_raw is not None and active_raw < 4 else None
        gcmd.respond_info('cfs_box_state=%s active_spool=%s' % (
            _to_hex(result['rx']), active_spool))

    cmd_CFS_FEED_SPOOL_help = (
        'Send feed command. Params: SPOOL=<1-4> [STEP=<0-255>] [ADDR=<1-255>]')
    def cmd_CFS_FEED_SPOOL(self, gcmd):
        self._require_connected(gcmd)
        spool = gcmd.get_int('SPOOL', minval=1, maxval=4)
        step = gcmd.get_int('STEP', 1, minval=0, maxval=0xFF)
        addr = self._gcmd_addr(gcmd)
        payload = bytes([spool - 1, step])
        result = self._query_frame(addr, 0x10, payload, wait=True)
        gcmd.respond_info('cfs_feed tx=%s rx=%s' % (
            _to_hex(result['tx']), _to_hex(result['rx'])))

    cmd_CFS_REWIND_SPOOL_help = (
        'Send rewind command. Params: SPOOL=<1-4> [STEP=<0-255>] [ADDR=<1-255>]')
    def cmd_CFS_REWIND_SPOOL(self, gcmd):
        self._require_connected(gcmd)
        spool = gcmd.get_int('SPOOL', minval=1, maxval=4)
        step = gcmd.get_int('STEP', 1, minval=0, maxval=0xFF)
        addr = self._gcmd_addr(gcmd)
        payload = bytes([spool - 1, step])
        result = self._query_frame(addr, 0x11, payload, wait=True)
        gcmd.respond_info('cfs_rewind tx=%s rx=%s' % (
            _to_hex(result['tx']), _to_hex(result['rx'])))

    cmd_CFS_RUNOUT_STATUS_help = 'Query filament sensor/runout state.'
    def cmd_CFS_RUNOUT_STATUS(self, gcmd):
        self._require_connected(gcmd)
        addr = self._gcmd_addr(gcmd)
        result = self._query_frame(addr, 0x08, b'', wait=True)
        pdata = result['parsed']['data']
        status = {
            'raw': _to_hex(result['rx']),
            'status_code': result['parsed']['status'],
            'fn': result['parsed']['fn'],
            'data_hex': _to_hex(pdata),
            'data_bytes': list(pdata),
        }
        gcmd.respond_info('cfs_runout=%s' % (status,))

    cmd_CFS_RFID_READ_help = (
        'Read RFID/NFC label data. Params: SPOOL=<1-4> [ADDR=<1-255>] '
        '[RAW=0|1]. RAW=0 sends slot-only payload, RAW=1 sends no payload.')
    def cmd_CFS_RFID_READ(self, gcmd):
        self._require_connected(gcmd)
        spool = gcmd.get_int('SPOOL', minval=1, maxval=4)
        addr = self._gcmd_addr(gcmd)
        raw = gcmd.get_int('RAW', 0, minval=0, maxval=1)
        payload = b'' if raw else bytes([spool - 1])
        result = self._query_frame(addr, self.rfid_read_fn, payload, wait=True)
        pdata = result['parsed']['data']
        rfid = {
            'raw': _to_hex(result['rx']),
            'status_code': result['parsed']['status'],
            'fn': result['parsed']['fn'],
            'slot': spool,
            'data_hex': _to_hex(pdata),
            'data_ascii': ''.join(chr(c) if 32 <= c <= 126 else '.'
                                  for c in pdata),
            'data_bytes': list(pdata),
        }
        gcmd.respond_info('cfs_rfid=%s' % (rfid,))

    cmd_CFS_RFID_WRITE_help = (
        'Write RFID/NFC label data. Params: SPOOL=<1-4> DATA_HEX=<hex> '
        '[ADDR=<1-255>] [RAW=0|1]. RAW=0 payload is [slot,op,data], '
        'RAW=1 sends DATA_HEX bytes as-is.')
    def cmd_CFS_RFID_WRITE(self, gcmd):
        self._require_connected(gcmd)
        spool = gcmd.get_int('SPOOL', minval=1, maxval=4)
        addr = self._gcmd_addr(gcmd)
        raw = gcmd.get_int('RAW', 0, minval=0, maxval=1)
        data = self._parse_hex(gcmd.get('DATA_HEX'), 'DATA_HEX')
        if not data:
            raise gcmd.error('DATA_HEX must not be empty')
        if raw:
            payload = data
        else:
            payload = bytes([spool - 1, self.rfid_write_op]) + data
        result = self._query_frame(addr, self.rfid_write_fn, payload, wait=True)
        ack = {
            'tx': _to_hex(result['tx']),
            'rx': _to_hex(result['rx']),
            'status_code': result['parsed']['status'],
            'fn': result['parsed']['fn'],
        }
        gcmd.respond_info('cfs_rfid_write=%s' % (ack,))


def load_config(config):
    return CfsConnection(config)