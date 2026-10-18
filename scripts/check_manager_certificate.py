#!/usr/bin/env python3
"""Inspect APK v2 certificate identity; this is not cryptographic APK verification."""
import hashlib
import json
from pathlib import Path
import struct
import sys


def lp32(data, offset=0):
    if offset + 4 > len(data):
        raise ValueError('Truncated length-prefixed APK structure')
    size = struct.unpack_from('<I', data, offset)[0]
    end = offset + 4 + size
    if end > len(data):
        raise ValueError('Invalid APK structure length')
    return data[offset + 4:end], end


def certificate(path):
    data = path.read_bytes()
    eocd = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557))
    if eocd < 0:
        raise ValueError('APK ZIP end-of-central-directory not found')
    central = struct.unpack_from('<I', data, eocd + 16)[0]
    if data[central - 16:central] != b'APK Sig Block 42':
        raise ValueError('APK signing block not found')
    size = struct.unpack_from('<Q', data, central - 24)[0]
    start = central - size - 8
    if start < 0 or struct.unpack_from('<Q', data, start)[0] != size:
        raise ValueError('APK signing block size mismatch')
    pos = start + 8
    blocks = []
    while pos < central - 24:
        length = struct.unpack_from('<Q', data, pos)[0]
        if length < 4 or pos + 8 + length > central - 24:
            raise ValueError('Invalid APK signing block entry')
        block_id = struct.unpack_from('<I', data, pos + 8)[0]
        if block_id == 0x7109871A:
            blocks.append(data[pos + 12:pos + 8 + length])
        pos += 8 + length
    if len(blocks) != 1:
        raise ValueError('Expected exactly one APK Signature Scheme v2 block')
    signers, _ = lp32(blocks[0])
    signer, end = lp32(signers)
    if end != len(signers):
        raise ValueError('Multiple signers are not supported by the manager policy')
    signed, _ = lp32(signer)
    _, digests_end = lp32(signed)
    certificates, _ = lp32(signed, digests_end)
    cert, _ = lp32(certificates)
    return cert


if __name__ == '__main__':
    cert = certificate(Path(sys.argv[1]))
    fingerprint = hashlib.sha256(cert).hexdigest()
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / 'sources.lock.json').read_text())
    result = {'certificate_der_size': len(cert), 'sha256': fingerprint,
              'matches_lock': fingerprint == lock['manager_certificate_sha256'] and
                              len(cert) == int(lock['manager_certificate_size'], 16)}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['matches_lock'] else 1)
