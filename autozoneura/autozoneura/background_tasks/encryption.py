import os
import json
import base64
import binascii
import frappe

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# Import AES key manager
from autozoneura.autozoneura.background_tasks.efris_key_manager import (
    get_private_key,
    resolve_file_path,
    PFX_PASSWORD,
    get_or_refresh_aes_key_only
)

CACHE_KEY_AES = "efris_cached_aes_key"

def get_cached_aes_key():
    """Retrieve the AES key from cache or regenerate using efris_key_manager."""
    aes_key_hex = frappe.cache().get_value(CACHE_KEY_AES)

    if not aes_key_hex:
        frappe.logger().warning("AES key not found in cache. Regenerating...")
        result = get_or_refresh_aes_key_only()
        if result.get("success"):
            aes_key_hex = result.get("aes_key")
        else:
            frappe.throw(f"Failed to regenerate AES key: {result.get('error')}")
        
        frappe.logger().info(f"✓ AES key regenerated (hex): {aes_key_hex}")
    else:
        frappe.logger().info(f"✓ AES key loaded from cache (hex): {aes_key_hex}")

    try:
        aes_key_bytes = binascii.unhexlify(aes_key_hex)
        frappe.logger().info(f"✓ AES key converted to bytes, length: {len(aes_key_bytes)} bytes")
        return aes_key_bytes
    except Exception as e:
        frappe.throw(f"Failed to convert AES key from hex to bytes: {str(e)}")

def encrypt_and_sign_payload(payload_dict, aes_key_bytes, private_key):
    json_str = json.dumps(payload_dict, separators=(',', ':'))
    plaintext_bytes = json_str.encode('utf-8')
    frappe.logger().info(f"Original JSON: {json_str}")

    # Print AES key being used
    aes_key_hex = aes_key_bytes.hex()
    frappe.logger().info(f"Using AES key for encryption (hex): {aes_key_hex}")
    print(f"[DEBUG] Using AES key for encryption (hex): {aes_key_hex}")

    # Padding
    padded_data = pad(plaintext_bytes, AES.block_size)
    frappe.logger().info(f"Padded data length: {len(padded_data)} bytes")

    # Encrypt
    cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
    ciphertext = cipher.encrypt(padded_data)
    content_b64 = base64.b64encode(ciphertext).decode('utf-8')

    frappe.logger().info(f"Ciphertext (hex): {ciphertext.hex()}")
    frappe.logger().info(f"Content B64: {content_b64}")
    print(f"[DEBUG] Encrypted Content (base64): {content_b64}")

    # Sign
    signature = private_key.sign(
        content_b64.encode('utf-8'),
        asym_padding.PKCS1v15(),
        hashes.SHA1()
    )
    signature_b64 = base64.b64encode(signature).decode('utf-8')
    frappe.logger().info("✓ Signature created")
    print(f"[DEBUG] Signature (base64): {signature_b64}")

    # Verify locally
    try:
        public_key = private_key.public_key()
        public_key.verify(
            signature,
            content_b64.encode('utf-8'),
            asym_padding.PKCS1v15(),
            hashes.SHA1()
        )
        frappe.logger().info("✓ Local signature verification PASSED")
    except Exception as verify_error:
        frappe.logger().error(f"✗ Local signature verification FAILED: {verify_error}")

    return {
        "content": content_b64,
        "signature": signature_b64
    }

@frappe.whitelist()
def encrypt_dynamic_json(json_input=None):
    try:
        company = frappe.defaults.get_user_default("company")
        if not company:
            frappe.throw("No default company set for the current session")

        settings = frappe.get_doc("EFRIS Settings", {"custom_company": company})
        if not settings.custom_private_key_:
            frappe.throw("Private key file not configured in EFRIS Settings")

        file_path = resolve_file_path(settings.custom_private_key_)
        private_key = get_private_key(file_path, PFX_PASSWORD)

        # Get AES key (cached or regenerate if missing)
        aes_key = get_cached_aes_key()

        # Load and parse input
        if isinstance(json_input, str):
            payload = json.loads(json_input)
        else:
            payload = json_input or {"sample": "data", "timestamp": frappe.utils.now()}

        result = encrypt_and_sign_payload(payload, aes_key, private_key)

        return {
            "success": True,
            "encrypted_content": result["content"],
            "signature": result["signature"]
        }

    except Exception as e:
        frappe.log_error(f"encrypt_dynamic_json error: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }