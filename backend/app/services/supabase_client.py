import re
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

import supabase._sync.client
original_match = re.match
def bypass_jwt_check(pattern, string, flags=0):
    if "A-Za-z0-9-_=" in str(pattern):
        return True
    return original_match(pattern, string, flags)

supabase._sync.client.re.match = bypass_jwt_check

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise ValueError("Supabase credentials missing.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase_client: Client = get_supabase_client()
else:
    supabase_client = None

supabase = supabase_client
