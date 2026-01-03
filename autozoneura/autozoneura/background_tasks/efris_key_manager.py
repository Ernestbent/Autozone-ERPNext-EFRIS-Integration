import base64
import json
import os
import requests
from datetime import datetime
import frappe
from frappe import cache, throw
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from Crypto.Cipher import PKCS1_v1_5, PKCS1_OAEP
from Crypto.PublicKey import RSA
import binascii

API_BASE_URL = "https://efristest.ura.go.ug/efrisws/ws/taapp/getInformation"
CACHE_KEY_AES = "efris_cached_aes_key"
PFX_PASSWORD = b"0772835195"

@frappe.whitelist()
def resolve_file_path(file_url):
    if not file_url:
        frappe.throw("No file URL provided")
    
    if file_url.startswith('/private/files/'):
        file_name = file_url.split("/")[-1]
        file_path = os.path.join(frappe.get_site_path("private", "files"), file_name)
    elif file_url.startswith('/files/'):
        file_name = file_url.split("/")[-1]
        file_path = os.path.join(frappe.get_site_path("public", "files"), file_name)
    else:
        file_name = file_url.split("/")[-1]
        file_path = os.path.join(frappe.get_site_path("private", "files"), file_name)
        if not os.path.exists(file_path):
            file_path = os.path.join(frappe.get_site_path("public", "files"), file_name)
    
    if not os.path.exists(file_path):
        frappe.throw(f"Private key file not found at: {file_path}")
    
    return file_path

@frappe.whitelist()
def get_private_key(pfx_path, password):
    with open(pfx_path, 'rb') as f:
        pfx_data = f.read()
    
    password_bytes = password if isinstance(password, bytes) else password.encode('utf-8')
    pfx = pkcs12.load_key_and_certificates(pfx_data, password_bytes, default_backend())
    private_key = pfx[0]
    
    if not private_key:
        raise Exception("Private key extraction failed")
    
    return private_key

@frappe.whitelist()
def get_AES_key(passwordDes_b64, private_key):
    passwordDes_encrypted = base64.b64decode(passwordDes_b64)
    
    # Method 1: PKCS1v15
    try:
        decrypted = private_key.decrypt(passwordDes_encrypted, padding.PKCS1v15())
        try:
            aes_key_raw = base64.b64decode(decrypted)
            return binascii.hexlify(aes_key_raw).decode('utf-8')
        except:
            return binascii.hexlify(decrypted).decode('utf-8')
    except:
        pass
    
    # Method 2: OAEP SHA1
    try:
        decrypted = private_key.decrypt(
            passwordDes_encrypted,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA1(),
                label=None
            )
        )
        try:
            aes_key_raw = base64.b64decode(decrypted)
            return binascii.hexlify(aes_key_raw).decode('utf-8')
        except:
            return binascii.hexlify(decrypted).decode('utf-8')
    except:
        pass
    
    # Method 3: PyCryptodome PKCS1_v1_5
    try:
        pkey_str = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption()
        )
        rsa_key = RSA.import_key(pkey_str)
        cipher = PKCS1_v1_5.new(rsa_key)
        decrypted = cipher.decrypt(passwordDes_encrypted, None)
        if decrypted:
            try:
                aes_key_raw = base64.b64decode(decrypted)
                return binascii.hexlify(aes_key_raw).decode('utf-8')
            except:
                return binascii.hexlify(decrypted).decode('utf-8')
    except:
        pass
    
    # Method 4: PyCryptodome OAEP
    try:
        cipher = PKCS1_OAEP.new(rsa_key)
        decrypted = cipher.decrypt(passwordDes_encrypted)
        try:
            aes_key_raw = base64.b64decode(decrypted)
            return binascii.hexlify(aes_key_raw).decode('utf-8')
        except:
            return binascii.hexlify(decrypted).decode('utf-8')
    except:
        pass
    
    raise Exception("All decryption methods failed")

@frappe.whitelist()
def make_t104_api_call(device_number, tin):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    payload = {
        "data": {"content": "", "signature": "", "dataDescription": {"codeType": "0", "encryptCode": "1", "zipCode": "0"}},
        "globalInfo": {
            "appId": "AP04",
            "version": "1.1.20191201",
            "dataExchangeId": "9230489223014123",
            "interfaceCode": "T104",
            "requestCode": "TP",
            "requestTime": current_time,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": device_number,
            "tin": tin,
            "brn": "",
            "taxpayerID": "1",
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {"responseDateFormat": "dd/MM/yyyy","responseTimeFormat": "dd/MM/yyyy HH:mm:ss","referenceNo":"24PL01000221","operatorName":"administrator"}
        },
        "returnStateInfo": {"returnCode": "", "returnMessage": ""}
    }
    
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    response = requests.post(API_BASE_URL, json=payload, headers=headers, timeout=30)
    
    if response.status_code != 200:
        raise Exception(f"T104 API call failed with status {response.status_code}")
    
    content = response.json().get("data", {}).get("content")
    if not content:
        raise Exception("Missing encrypted content in T104 response")
    
    return json.loads(base64.b64decode(content).decode())


@frappe.whitelist()
def test_efris_complete_flow():
    try:
        company = frappe.defaults.get_user_default("company")
        if not company:
            return {"success": False, "error": "No default company"}
        
        settings = frappe.get_doc("EFRIS Settings", {"company": company})
        file_path = resolve_file_path(settings.private_key)
        
        try:
            private_key = get_private_key(file_path, PFX_PASSWORD)
        except:
            private_key = get_private_key(file_path, b"")
        
        t104_response = make_t104_api_call(settings.device_number, settings.tin)
        password_des = t104_response.get("passowrdDes")
        if not password_des:
            return {"success": False, "error": "Missing passwordDes"}
        
        aes_key_hex = get_AES_key(password_des, private_key)
        cache().set_value(CACHE_KEY_AES, aes_key_hex, expires_in_sec=86400)
        
        return {
            "success": True,
            "company": company,
            "device_number": settings.device_number,
            "tin": settings.tin,
            "aes_key": aes_key_hex,
            "cached": True
        }
        
    except Exception as e:
        frappe.log_error(f"EFRIS Test Error: {str(e)[:100]}")
        return {"success": False, "error": str(e)}
