import requests

SUPABASE_URL = "https://ygbwqhxxmfdvrenbpcnw.supabase.co"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlnYndxaHh4bWZkdnJlbmJwY253Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDMyNTg1MSwiZXhwIjoyMDk1OTAxODUxfQ.2utS63jpCKsvvrEHARdG6szDiRrzx98UasX3aXiZvzI"

headers = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
}

print("Checking admin tables...")
for table in ['user_activity', 'user_sessions', 'user_profiles']:
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?limit=0", headers=headers)
    print(f"  {table}: {'OK' if r.status_code == 200 else f'FAIL ({r.status_code})'}")
