## EFRIS Cache Utility to get or refresh the AES encryption key
import frappe
import binascii

CACHE_KEY_AES = "efris_cached_aes_key"

def get_aes_key_from_cache():
    """
    Get the AES key from cache as bytes.
    If not found, attempts to regenerate it.
    
    Returns:
        bytes: AES key as bytes
    
    Raises:
        frappe.ValidationError: If key cannot be retrieved or regenerated
    """
    key_hex = frappe.cache().get_value(CACHE_KEY_AES)
    
    if not key_hex:
        # Try to regenerate the key
        frappe.msgprint("AES key not found in cache. Attempting to regenerate...", indicator="orange")
        
        try:
            from autozoneura.autozoneura.background_tasks.efris_key_manager import test_efris_complete_flow
            result = test_efris_complete_flow()
            
            if result.get("success"):
                key_hex = result.get("aes_key")
                frappe.msgprint("AES key regenerated successfully!", indicator="green")
            else:
                error_msg = result.get("error", "Unknown error")
                frappe.throw(f"Failed to regenerate AES key: {error_msg}")
        except Exception as e:
            frappe.throw(f"AES key not found in cache and regeneration failed: {str(e)}")
    
    # Convert hex string to bytes
    try:
        key_bytes = bytes.fromhex(key_hex)
        
        # Validate key length (AES supports 16, 24, or 32 bytes)
        if len(key_bytes) not in [16, 24, 32]:
            frappe.throw(f"Invalid AES key length: {len(key_bytes)} bytes. Expected 16, 24, or 32 bytes.")
        
        return key_bytes
    except Exception as e:
        frappe.throw(f"Failed to convert AES key from hex to bytes: {str(e)}")

def get_aes_key_hex_from_cache():
    """
    Get the AES key from cache as hex string.
    
    Returns:
        str: AES key as hex string
    """
    key_hex = frappe.cache().get_value(CACHE_KEY_AES)
    
    if not key_hex:
        # Try to regenerate
        try:
            from autozoneura.autozoneura.background_tasks.efris_key_manager import test_efris_complete_flow
            result = test_efris_complete_flow()
            
            if result.get("success"):
                key_hex = result.get("aes_key")
            else:
                frappe.throw(f"Failed to get AES key: {result.get('error', 'Unknown error')}")
        except Exception as e:
            frappe.throw(f"AES key not found and regeneration failed: {str(e)}")
    
    return key_hex

def set_aes_key_in_cache(aes_key_hex, expires_in_hours=24):
    """
    Set the AES key in cache.
    
    Args:
        aes_key_hex (str): AES key as hex string
        expires_in_hours (int): How many hours until the key expires (default: 24)
    
    Returns:
        bool: True if successful
    """
    expires_in_sec = expires_in_hours * 3600
    frappe.cache().set_value(CACHE_KEY_AES, aes_key_hex, expires_in_sec=expires_in_sec)
    return True

def is_aes_key_cached():
    """
    Check if AES key exists in cache.
    
    Returns:
        bool: True if key exists in cache
    """
    key_hex = frappe.cache().get_value(CACHE_KEY_AES)
    return key_hex is not None

def clear_aes_key_from_cache():
    """
    Clear the AES key from cache.
    Useful for forcing a refresh.
    
    Returns:
        bool: True if successful
    """
    frappe.cache().delete_value(CACHE_KEY_AES)
    return True

@frappe.whitelist()
def refresh_aes_key():
    """
    Manually refresh the AES key.
    Can be called from anywhere.
    
    Returns:
        dict: Result with success status and message
    """
    try:
        from autozoneura.autozoneura.background_tasks.efris_key_manager import test_efris_complete_flow
        result = test_efris_complete_flow()
        
        if result.get("success"):
            return {
                "success": True,
                "message": "AES key refreshed successfully",
                "aes_key": result.get("aes_key")
            }
        else:
            return {
                "success": False,
                "message": f"Failed to refresh AES key: {result.get('error')}"
            }
    except Exception as e:
        frappe.log_error(f"AES Key Refresh Error: {str(e)}", "EFRIS AES Key")
        return {
            "success": False,
            "message": str(e)
        }