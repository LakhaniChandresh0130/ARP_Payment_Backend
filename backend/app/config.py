import os
from dotenv import load_dotenv

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# Read either the specific role key or the secret key they provided
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "openai/gpt-oss-20b")
APP_URL = os.getenv("APP_URL", "http://localhost:3000")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("Warning: Supabase credentials not found in environment variables.")
