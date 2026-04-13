import json
import base64
import hashlib
import requests
import argparse
import struct
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from Crypto.Cipher import AES

def get_hashed_advertisement_key(private_key_b64):
    """
    Derives the hashed advertisement key from a base64 encoded private key.
    """
    private_key_bytes = base64.b64decode(private_key_b64)
    private_key_int = int.from_bytes(private_key_bytes, 'big')
    
    private_key = ec.derive_private_key(private_key_int, ec.SECP224R1())
    public_key = private_key.public_key()
    
    public_key_x = public_key.public_numbers().x.to_bytes(28, 'big')
    
    hashed_ad_key = hashlib.sha256(public_key_x).digest()
    
    return base64.b64encode(hashed_ad_key).decode('utf-8')

def decrypt_report(report, private_key_b64):
    """
    Decrypts a FindMy report.
    """
    try:
        payload = base64.b64decode(report['payload'])
        
        # Extract parts of the payload
        ephemeral_key_bytes = payload[-83:-26]
        enc_data = payload[-26:-16]
        tag = payload[-16:]

        # Decode private key
        private_key_bytes = base64.b64decode(private_key_b64)
        private_key_int = int.from_bytes(private_key_bytes, 'big')
        private_key = ec.derive_private_key(private_key_int, ec.SECP224R1())

        # Decode ephemeral public key
        ephemeral_public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP224R1(), ephemeral_key_bytes)

        # ECDH
        shared_key_bytes = private_key.exchange(ec.ECDH(), ephemeral_public_key)

        # KDF
        counter = (1).to_bytes(4, 'big')
        kdf_input = shared_key_bytes + counter + ephemeral_key_bytes
        derived_key = hashlib.sha256(kdf_input).digest()

        decryption_key = derived_key[:16]
        iv = derived_key[16:]

        # AES-GCM Decryption
        cipher = AES.new(decryption_key, AES.MODE_GCM, nonce=iv, mac_len=16)
        decrypted_payload = cipher.decrypt_and_verify(enc_data, tag)

        # Decode payload
        latitude = struct.unpack('>I', decrypted_payload[0:4])[0] / 10000000.0
        longitude = struct.unpack('>I', decrypted_payload[4:8])[0] / 10000000.0
        accuracy = decrypted_payload[8]

        # Correct coordinates
        if latitude > 90: latitude -= 0xFFFFFFFF / 10000000.0
        if latitude < -90: latitude += 0xFFFFFFFF / 10000000.0
        if longitude > 180: longitude -= 0xFFFFFFFF / 10000000.0
        if longitude < -180: longitude += 0xFFFFFFFF / 10000000.0

        # Decode timestamp and confidence
        seen_timestamp_s = struct.unpack('>i', payload[0:4])[0]
        timestamp = datetime(2001, 1, 1) + timedelta(seconds=seen_timestamp_s)
        confidence = payload[4]

        return {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "timestamp": timestamp.isoformat(),
            "confidence": confidence
        }

    except Exception as e:
        return {"error": str(e)}


def fetch_reports(hashed_keys_map, url, days=7, user=None, password=None):
    """
    Fetches location reports from the server.
    """
    hashed_keys = list(hashed_keys_map.keys())
    payload = {
        "ids": hashed_keys,
        "days": days
    }
    
    headers = {
        "Content-Type": "application/json"
    }

    auth = None
    if user and password:
        auth = (user, password)

    print(f"Requesting reports for {len(hashed_keys)} keys from {url}...")
    
    try:
        response = requests.post(url, json=payload, headers=headers, auth=auth)
        response.raise_for_status()
        
        results = response.json().get("results", [])
        print(f"Found {len(results)} reports.")
        return results
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching reports: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Fetch and decrypt FindMy location reports.")
    parser.add_argument("json_file", help="Path to the JSON file with private keys.")
    parser.add_argument("--url", default="http://localhost:6176", help="URL of the FindMy server.")
    parser.add_argument("--days", type=int, default=7, help="Number of days of reports to fetch.")
    parser.add_argument("--user", help="Username for server authentication.")
    parser.add_argument("--password", help="Password for server authentication.")
    
    args = parser.parse_args()
    
    try:
        with open(args.json_file, 'r') as f:
            keys_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: JSON file not found at {args.json_file}")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {args.json_file}")
        return

    hashed_keys_map = {}
    for item in keys_data:
        keys_to_process = []
        if "privateKey" in item and item["privateKey"]:
            keys_to_process.append(item["privateKey"])
        
        if "additionalKeys" in item and isinstance(item["additionalKeys"], list):
            keys_to_process.extend(item["additionalKeys"])

        for key in keys_to_process:
            try:
                hashed_key = get_hashed_advertisement_key(key)
                hashed_keys_map[hashed_key] = key
            except Exception as e:
                print(f"Could not process a key for item '{item.get('name', 'Unnamed')}': {e}")

    if not hashed_keys_map:
        print("No valid private keys found in the JSON file.")
        return
        
    reports = fetch_reports(hashed_keys_map, args.url, args.days, args.user, args.password)
    
    if reports:
        print("\n--- Reports ---")
        for report in reports:
            private_key_b64 = hashed_keys_map.get(report['id'])
            if private_key_b64:
                decrypted_data = decrypt_report(report, private_key_b64)
                report['decrypted'] = decrypted_data
            print(json.dumps(report, indent=2))
        print("---------------")

if __name__ == "__main__":
    main()
