import base64
import hashlib
import struct
from datetime import datetime, timedelta

import requests
from cryptography.hazmat.primitives.asymmetric import ec

try:
    from Cryptodome.Cipher import AES
except Exception:
    from Crypto.Cipher import AES


def _get_hashed_advertisement_key(private_key_b64):
    private_key_bytes = base64.b64decode(private_key_b64)
    private_key_int = int.from_bytes(private_key_bytes, "big")
    private_key = ec.derive_private_key(private_key_int, ec.SECP224R1())
    public_key = private_key.public_key()
    public_key_x = public_key.public_numbers().x.to_bytes(28, "big")
    hashed_ad_key = hashlib.sha256(public_key_x).digest()
    return base64.b64encode(hashed_ad_key).decode("utf-8")


def _decrypt_report(report, private_key_b64):
    payload = base64.b64decode(report["payload"])

    ephemeral_key_bytes = payload[-83:-26]
    enc_data = payload[-26:-16]
    tag = payload[-16:]

    private_key_bytes = base64.b64decode(private_key_b64)
    private_key_int = int.from_bytes(private_key_bytes, "big")
    private_key = ec.derive_private_key(private_key_int, ec.SECP224R1())

    ephemeral_public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP224R1(), ephemeral_key_bytes)
    shared_key_bytes = private_key.exchange(ec.ECDH(), ephemeral_public_key)

    counter = (1).to_bytes(4, "big")
    kdf_input = shared_key_bytes + counter + ephemeral_key_bytes
    derived_key = hashlib.sha256(kdf_input).digest()

    decryption_key = derived_key[:16]
    iv = derived_key[16:]

    cipher = AES.new(decryption_key, AES.MODE_GCM, nonce=iv, mac_len=16)
    decrypted_payload = cipher.decrypt_and_verify(enc_data, tag)

    latitude = struct.unpack(">I", decrypted_payload[0:4])[0] / 10000000.0
    longitude = struct.unpack(">I", decrypted_payload[4:8])[0] / 10000000.0
    accuracy = decrypted_payload[8]

    if latitude > 90:
        latitude -= 0xFFFFFFFF / 10000000.0
    if latitude < -90:
        latitude += 0xFFFFFFFF / 10000000.0
    if longitude > 180:
        longitude -= 0xFFFFFFFF / 10000000.0
    if longitude < -180:
        longitude += 0xFFFFFFFF / 10000000.0

    seen_timestamp_s = struct.unpack(">i", payload[0:4])[0]
    timestamp = datetime(2001, 1, 1) + timedelta(seconds=seen_timestamp_s)
    confidence = payload[4]

    return {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "timestamp": timestamp.isoformat(),
        "confidence": confidence,
    }


def _build_key_map(accessories_rows):
    hashed_keys_map = {}
    source_map = {}

    for item in accessories_rows:
        keys_to_process = []
        tag_name = item.get("name") or str(item.get("id") or "Unknown")

        private_key = item.get("privateKey")
        if isinstance(private_key, str) and private_key:
            keys_to_process.append(private_key)

        additional_keys = item.get("additionalKeys") or []
        if isinstance(additional_keys, list):
            keys_to_process.extend([k for k in additional_keys if isinstance(k, str) and k])

        for key in keys_to_process:
            hashed = _get_hashed_advertisement_key(key)
            hashed_keys_map[hashed] = key
            source_map[hashed] = {
                "tag": tag_name,
                "file": item.get("source_file", ""),
            }

    return hashed_keys_map, source_map


def _chunked(seq, size):
    if size <= 0:
        size = 1
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _key_id_variants(value):
    if not isinstance(value, str) or not value:
        return []
    raw = value.strip()
    if not raw:
        return []

    variants = [raw]

    no_padding = raw.rstrip("=")
    if no_padding and no_padding not in variants:
        variants.append(no_padding)

    urlsafe = raw.replace("+", "-").replace("/", "_")
    if urlsafe not in variants:
        variants.append(urlsafe)

    urlsafe_no_padding = urlsafe.rstrip("=")
    if urlsafe_no_padding and urlsafe_no_padding not in variants:
        variants.append(urlsafe_no_padding)

    return variants


def _build_lookup_map(hashed_keys_map, source_map):
    lookup = {}
    for hashed, private_key in hashed_keys_map.items():
        source = source_map.get(hashed, {})
        for variant in _key_id_variants(hashed):
            lookup[variant] = {
                "private_key": private_key,
                "source": source,
                "canonical_id": hashed,
            }
    return lookup


def fetch_apple_locations(haystack_cfg, accessories_rows, days=7, timeout=30, batch_size=200):
    endpoint = (haystack_cfg or {}).get("endpoint", "").strip()
    if not endpoint:
        raise ValueError("haystack endpoint is not configured")

    if not accessories_rows:
        raise ValueError("no accessories uploaded")

    hashed_keys_map, source_map = _build_key_map(accessories_rows)
    if not hashed_keys_map:
        raise ValueError("no valid Apple key material found in uploaded accessories")

    ids = list(hashed_keys_map.keys())
    query_days = int(days)
    batch_size = int(batch_size)

    auth = None
    login = (haystack_cfg or {}).get("login", "")
    password = (haystack_cfg or {}).get("password", "")
    if login and password:
        auth = (login, password)

    reports = []
    batch_count = 0
    for ids_batch in _chunked(ids, batch_size):
        batch_count += 1
        payload = {
            "ids": ids_batch,
            "days": query_days,
        }
        response = requests.post(endpoint, json=payload, timeout=timeout, auth=auth)
        response.raise_for_status()
        body = response.json()
        batch_reports = body.get("results", []) if isinstance(body, dict) else []
        if isinstance(batch_reports, list):
            reports.extend(batch_reports)

    lookup_map = _build_lookup_map(hashed_keys_map, source_map)

    decoded = []
    failed = []
    for report in reports:
        key_id = report.get("id") if isinstance(report, dict) else None
        lookup = lookup_map.get(key_id)
        if not lookup:
            failed.append({"reason": "missing_private_key", "report": report})
            continue

        try:
            entry = _decrypt_report(report, lookup["private_key"])
            entry["key_id"] = key_id
            entry["canonical_key_id"] = lookup["canonical_id"]
            entry["source"] = lookup["source"]
            decoded.append(entry)
        except Exception as exc:
            failed.append({"reason": str(exc), "report": report})

    decoded.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "provider": "apple",
        "query": {
            "ids": len(hashed_keys_map),
            "days": query_days,
            "batch_size": batch_size,
            "batches": batch_count,
        },
        "counts": {
            "reports_total": len(reports),
            "decoded": len(decoded),
            "failed": len(failed),
        },
        "latest": decoded[0] if decoded else None,
        "reports": decoded,
        "failures": failed,
    }
