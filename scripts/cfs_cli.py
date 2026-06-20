#!/usr/bin/env python3
# Simple CLI test client for Creality CFS RS485 protocol
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# That's AI generated client. Need review it with HW.

import argparse
import typer
from typing_extensions import Annotated
import binascii
import sys

import serial


def _to_hex(data):
    return binascii.hexlify(data).decode('ascii').upper()


def _parse_hex(value, field_name, default=b""):
    if value is None:
        return default
    if value is None:
        return b''
    cleaned = ''.join(value.split())
    if not cleaned:
        return b''
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise SystemExit("Invalid %s: '%s'" % (field_name, value)) from exc


def _crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07 if crc & 0x80 else crc << 1) & 0xFF
    return crc


def _build_frame(addr, status, fn, data=b''):
    payload = bytes(data)
    length = 3 + len(payload)
    body = bytes([length & 0xFF, status & 0xFF, fn & 0xFF]) + payload
    return bytes([0xF7, addr & 0xFF]) + body + bytes([_crc8(body)])


def _parse_frame(data):
    if not data or len(data) < 6 or data[0] != 0xF7:
        raise ValueError('invalid cfs frame')
    length = data[2]
    total = length + 3
    if len(data) < total:
        raise ValueError('short cfs frame')
    frame = data[:total]
    expected = _crc8(frame[2:-1])
    if expected != frame[-1]:
        raise ValueError('crc mismatch: %02X != %02X' % (expected, frame[-1]))
    return {
        'raw': frame,
        'addr': frame[1],
        'length': frame[2],
        'status': frame[3],
        'fn': frame[4],
        'data': frame[5:-1],
        'crc': frame[-1],
    }


def _recv_once(ser, timeout):
    prev = ser.timeout
    try:
        ser.timeout = timeout
        data = ser.read(256)
    finally:
        ser.timeout = prev
    if not data:
        raise TimeoutError('cfs response timeout')
    return data


def _query_frame(ser, addr, fn, data=b'', status=0xFF, timeout=3.0):
    tx = _build_frame(addr, status, fn, data)
    ser.reset_input_buffer()
    ser.write(tx)
    ser.flush()
    rx = _recv_once(ser, timeout)
    parsed = _parse_frame(rx)
    return tx, rx, parsed


def _open_serial(args):
    return serial.Serial(
        args.serial,
        args.baud,
        timeout=args.io_timeout,
        write_timeout=args.io_timeout,
    )


@app.command()
def status(
    serial_port: SerialOption,
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    ping: Annotated[
        bool,
        typer.Option("--ping", help="Send a ping-style query to verify connectivity"),
    ] = False,
    ping_fn: Annotated[
        str,
        typer.Option("--ping-fn", help="Function code used by --ping"),
    ] = "0xA2",
):
    """Print client config; optional ping query."""
    addr_value = _parse_int(addr)
    ping_fn_value = _parse_int(ping_fn)
    print(
        "serial=%s baud=%d io_timeout=%.3f rsp_timeout=%.3f addr=%d"
        % (serial_port, baud, io_timeout, response_timeout, addr_value)
    )
    if not ping:
        return
    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser, addr_value, ping_fn_value, b"", timeout=response_timeout
        )
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    print('serial=%s baud=%d io_timeout=%.3f rsp_timeout=%.3f addr=%d'
          % (args.serial, args.baud, args.io_timeout,
             args.response_timeout, args.addr))
    if not args.ping:
        return 0
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, args.ping_fn, b'', timeout=args.response_timeout)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    return 0


@app.command("send-hex")
def send_hex(
    serial_port: SerialOption,
    hex_value: Annotated[str, typer.Option("--hex", help="Raw bytes in hex form")],
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    wait: Annotated[
        bool,
        typer.Option("--wait", help="Wait for one response after send"),
    ] = False,
    parse: Annotated[
        bool,
        typer.Option("--parse", help="Parse received bytes as a CFS frame"),
    ] = False,
):
    """Send raw hex bytes."""
    _parse_int(addr)
    payload = _parse_hex(hex_value, "HEX")

    with _open_serial(serial_port, baud, io_timeout) as ser:
        ser.reset_input_buffer()
        ser.write(payload)
        ser.flush()
        print("tx=%s" % (_to_hex(payload),))
        if wait:
            rx = _recv_once(ser, response_timeout)
            print("rx=%s" % (_to_hex(rx),))
            if parse:
                print("parsed=%s" % (_parse_frame(rx),))
    payload = _parse_hex(args.hex, 'HEX')
    with _open_serial(args) as ser:
        ser.reset_input_buffer()
        ser.write(payload)
        ser.flush()
        print('tx=%s' % (_to_hex(payload),))
        if args.wait:
            rx = _recv_once(ser, args.response_timeout)
            print('rx=%s' % (_to_hex(rx),))
            if args.parse:
                print('parsed=%s' % (_parse_frame(rx),))
    return 0


@app.command()
def query(
    serial_port: SerialOption,
    fn: Annotated[str, typer.Option("--fn", help="Function code, decimal or 0xNN")],
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    status_value: Annotated[
        str,
        typer.Option("--status", help="Status byte in request"),
    ] = "0xFF",
    data_hex: Annotated[
        str,
        typer.Option("--data-hex", help="Payload bytes in hex"),
    ] = "",
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="Wait for response"),
    ] = True,
):
    """Send generic CFS frame."""
    addr_value = _parse_int(addr)
    fn_value = _parse_int(fn)
    status_byte = _parse_int(status_value)
    data = _parse_hex(data_hex, "DATA_HEX")
    
    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx = _build_frame(addr_value, status_byte, fn_value, data)
        ser.reset_input_buffer()
        ser.write(tx)
        ser.flush()
        print("tx=%s" % (_to_hex(tx),))
        if wait:
            rx = _recv_once(ser, response_timeout)
            print("rx=%s" % (_to_hex(rx),))
            print("parsed=%s" % (_parse_frame(rx),))
    data = _parse_hex(args.data_hex, 'DATA_HEX')
    with _open_serial(args) as ser:
        tx = _build_frame(args.addr, args.status, args.fn, data)
        ser.reset_input_buffer()
        ser.write(tx)
        ser.flush()
        print('tx=%s' % (_to_hex(tx),))
        if args.wait:
            rx = _recv_once(ser, args.response_timeout)
            print('rx=%s' % (_to_hex(rx),))
            print('parsed=%s' % (_parse_frame(rx),))
    return 0


def cmd_select_spool(args):
    payload = bytes([args.spool - 1, args.mode])
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, 0x04, payload, timeout=args.response_timeout)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    return 0


@app.command("get-active-spool")
def get_active_spool(
    serial_port: SerialOption,
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
):
    """Equivalent to CFS_GET_ACTIVE_SPOOL."""
    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser, _parse_int(addr), 0x0A, b"", timeout=response_timeout
        )
    pdata = parsed["data"]
    active_raw = pdata[0] if pdata else None
    active_spool = active_raw + 1 if active_raw is not None and active_raw < 4 else None
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    print("active_spool=%s" % (active_spool,))
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, 0x0A, b'', timeout=args.response_timeout)
    pdata = parsed['data']
    active_raw = pdata[0] if pdata else None
    active_spool = (active_raw + 1) if active_raw is not None and active_raw < 4 else None
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    print('active_spool=%s' % (active_spool,))
    return 0


@app.command("feed-spool")
def feed_spool(
    serial_port: SerialOption,
    spool: Annotated[int, typer.Option("--spool", help="Spool number: 1-4")],
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    step: Annotated[str, typer.Option("--step")] = "1",
):
    """Equivalent to CFS_FEED_SPOOL."""
    spool = _validate_spool(spool)
    payload = bytes([spool - 1, _parse_int(step)])

    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser, _parse_int(addr), 0x10, payload, timeout=response_timeout
        )
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    payload = bytes([args.spool - 1, args.step])
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, 0x10, payload, timeout=args.response_timeout)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    return 0


@app.command("rewind-spool")
def rewind_spool(
    serial_port: SerialOption,
    spool: Annotated[int, typer.Option("--spool", help="Spool number: 1-4")],
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    step: Annotated[str, typer.Option("--step")] = "1",
):
    """Equivalent to CFS_REWIND_SPOOL."""
    spool = _validate_spool(spool)
    payload = bytes([spool - 1, _parse_int(step)])

    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser, _parse_int(addr), 0x11, payload, timeout=response_timeout
        )
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    payload = bytes([args.spool - 1, args.step])
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, 0x11, payload, timeout=args.response_timeout)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    return 0


@app.command("runout-status")
def runout_status(
    serial_port: SerialOption,
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
):
    """Equivalent to CFS_RUNOUT_STATUS."""
    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser, _parse_int(addr), 0x08, b"", timeout=response_timeout
        )
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    print("data_hex=%s data_bytes=%s" % (_to_hex(parsed["data"]), list(parsed["data"])))
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, 0x08, b'', timeout=args.response_timeout)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    print('data_hex=%s data_bytes=%s' % (_to_hex(parsed['data']), list(parsed['data'])))
    return 0


@app.command("rfid-read")
def rfid_read(
    serial_port: SerialOption,
    spool: Annotated[int, typer.Option("--spool", help="Spool number: 1-4")],
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Read with empty payload instead of [slot] payload"),
    ] = False,
    rfid_read_fn: Annotated[
        str,
        typer.Option("--rfid-read-fn", help="RFID read function code"),
    ] = "0x02",
):
    """Equivalent to CFS_RFID_READ."""
    spool = _validate_spool(spool)
    payload = b"" if raw else bytes([spool - 1])

    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser,
            _parse_int(addr),
            _parse_int(rfid_read_fn),
            payload,
            timeout=response_timeout,
        )

    data = parsed["data"]
    ascii_data = "".join(chr(c) if 32 <= c <= 126 else "." for c in data)
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    print("data_hex=%s data_ascii=%s data_bytes=%s" % (_to_hex(data), ascii_data, list(data)))
    payload = b'' if args.raw else bytes([args.spool - 1])
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, args.rfid_read_fn, payload,
            timeout=args.response_timeout)
    data = parsed['data']
    ascii_data = ''.join(chr(c) if 32 <= c <= 126 else '.' for c in data)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    print('data_hex=%s data_ascii=%s data_bytes=%s'
          % (_to_hex(data), ascii_data, list(data)))
    return 0


@app.command("rfid-write")
def rfid_write(
    serial_port: SerialOption,
    spool: Annotated[int, typer.Option("--spool", help="Spool number: 1-4")],
    data_hex: Annotated[str, typer.Option("--data-hex", help="RFID write data in hex")],
    baud: BaudOption = 230400,
    io_timeout: IoTimeoutOption = 0.25,
    response_timeout: ResponseTimeoutOption = 3.0,
    addr: AddrOption = "0x01",
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Send DATA_HEX as raw payload"),
    ] = False,
    rfid_write_fn: Annotated[
        str,
        typer.Option("--rfid-write-fn", help="RFID write function code"),
    ] = "0x02",
    rfid_write_op: Annotated[
        str,
        typer.Option("--rfid-write-op", help="RFID write op byte when not in --raw mode"),
    ] = "0x01",
):
    """Equivalent to CFS_RFID_WRITE."""
    spool = _validate_spool(spool)
    data = _parse_hex(data_hex, "DATA_HEX")
    if not data:
        raise typer.BadParameter("DATA_HEX must not be empty")

    if raw:
        payload = data
    else:
        payload = bytes([spool - 1, _parse_int(rfid_write_op)]) + data

    with _open_serial(serial_port, baud, io_timeout) as ser:
        tx, rx, parsed = _query_frame(
            ser,
            _parse_int(addr),
            _parse_int(rfid_write_fn),
            payload,
            timeout=response_timeout,
        )
    print("tx=%s" % (_to_hex(tx),))
    print("rx=%s" % (_to_hex(rx),))
    print("parsed=%s" % (parsed,))
    data = _parse_hex(args.data_hex, 'DATA_HEX')
    if not data:
        raise SystemExit('DATA_HEX must not be empty')
    if args.raw:
        payload = data
    else:
        payload = bytes([args.spool - 1, args.rfid_write_op]) + data
    with _open_serial(args) as ser:
        tx, rx, parsed = _query_frame(
            ser, args.addr, args.rfid_write_fn, payload,
            timeout=args.response_timeout)
    print('tx=%s' % (_to_hex(tx),))
    print('rx=%s' % (_to_hex(rx),))
    print('parsed=%s' % (parsed,))
    return 0


def _add_common_options(parser):
    parser.add_argument('--serial', required=True,
                        help='Serial port path (example: /dev/ttyUSB0)')
    parser.add_argument('--baud', type=int, default=230400,
                        help='Serial baudrate (default: 230400)')
    parser.add_argument('--io-timeout', type=float, default=0.25,
                        help='Per-IO serial timeout in seconds (default: 0.25)')
    parser.add_argument('--response-timeout', type=float, default=3.0,
                        help='Response wait timeout in seconds (default: 3.0)')
    parser.add_argument('--addr', type=lambda x: int(x, 0), default=0x01,
                        help='CFS address (decimal or 0xNN; default: 0x01)')


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description='CLI test client for Creality CFS commands')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('status', help='Print client config; optional ping query')
    _add_common_options(p)
    p.add_argument('--ping', action='store_true',
                   help='Send a ping-style query to verify connectivity')
    p.add_argument('--ping-fn', type=lambda x: int(x, 0), default=0xA2,
                   help='Function code used by --ping (default: 0xA2)')
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('send-hex', help='Send raw hex bytes')
    _add_common_options(p)
    p.add_argument('--hex', required=True, help='Raw bytes in hex form')
    p.add_argument('--wait', action='store_true',
                   help='Wait for one response after send')
    p.add_argument('--parse', action='store_true',
                   help='Parse received bytes as a CFS frame (requires --wait)')
    p.set_defaults(func=cmd_send_hex)

    p = sub.add_parser('query', help='Send generic CFS frame')
    _add_common_options(p)
    p.add_argument('--fn', type=lambda x: int(x, 0), required=True,
                   help='Function code (0-255 or 0xNN)')
    p.add_argument('--status', type=lambda x: int(x, 0), default=0xFF,
                   help='Status byte in request (default: 0xFF)')
    p.add_argument('--data-hex', default='', help='Payload bytes in hex')
    p.add_argument('--wait', action='store_true', default=True,
                   help='Wait for response (default: true)')
    p.add_argument('--no-wait', dest='wait', action='store_false',
                   help='Do not wait for response')
    p.set_defaults(func=cmd_query)

    p = sub.add_parser('select-spool', help='Equivalent to CFS_SELECT_SPOOL')
    _add_common_options(p)
    p.add_argument('--spool', type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument('--mode', type=lambda x: int(x, 0), default=1)
    p.set_defaults(func=cmd_select_spool)

    p = sub.add_parser('get-active-spool',
                       help='Equivalent to CFS_GET_ACTIVE_SPOOL')
    _add_common_options(p)
    p.set_defaults(func=cmd_get_active_spool)

    p = sub.add_parser('feed-spool', help='Equivalent to CFS_FEED_SPOOL')
    _add_common_options(p)
    p.add_argument('--spool', type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument('--step', type=lambda x: int(x, 0), default=1)
    p.set_defaults(func=cmd_feed_spool)

    p = sub.add_parser('rewind-spool', help='Equivalent to CFS_REWIND_SPOOL')
    _add_common_options(p)
    p.add_argument('--spool', type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument('--step', type=lambda x: int(x, 0), default=1)
    p.set_defaults(func=cmd_rewind_spool)

    p = sub.add_parser('runout-status', help='Equivalent to CFS_RUNOUT_STATUS')
    _add_common_options(p)
    p.set_defaults(func=cmd_runout_status)

    p = sub.add_parser('rfid-read', help='Equivalent to CFS_RFID_READ')
    _add_common_options(p)
    p.add_argument('--spool', type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument('--raw', action='store_true',
                   help='Read with empty payload instead of [slot] payload')
    p.add_argument('--rfid-read-fn', type=lambda x: int(x, 0), default=0x02,
                   help='RFID read function code (default: 0x02)')
    p.set_defaults(func=cmd_rfid_read)

    p = sub.add_parser('rfid-write', help='Equivalent to CFS_RFID_WRITE')
    _add_common_options(p)
    p.add_argument('--spool', type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument('--data-hex', required=True, help='RFID write data in hex')
    p.add_argument('--raw', action='store_true',
                   help='Send DATA_HEX as raw payload')
    p.add_argument('--rfid-write-fn', type=lambda x: int(x, 0), default=0x02,
                   help='RFID write function code (default: 0x02)')
    p.add_argument('--rfid-write-op', type=lambda x: int(x, 0), default=0x01,
                   help='RFID write op byte when not in --raw mode (default: 0x01)')
    p.set_defaults(func=cmd_rfid_write)

    return parser


def main():
    try:
        app()
    except (serial.SerialException, TimeoutError, ValueError) as exc:
        sys.stderr.write("ERROR: %s\n" % (exc,))
        raise typer.Exit(2) from exc


if __name__ == '__main__':
    raise SystemExit(main())