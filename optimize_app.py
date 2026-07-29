import codecs, re

c = codecs.open('app.py','r','utf-8').read()

fixes = []

# Fix 1: Move `import re` inside inject_tracking_pixel to top
if '    import re\r\n    def rewrite_link' in c:
    c = c.replace('    import re\r\n    def rewrite_link', '    def rewrite_link')
    fixes.append('removed inline import re')

# Fix 2: Move `import urllib.parse` inside inject_tracking_pixel to top  
if '    import urllib.parse\r\n        encoded' in c:
    c = c.replace('    import urllib.parse\r\n        encoded', '        encoded')
    fixes.append('removed inline import urllib.parse')

# Fix 3: Remove inline `import requests` from call_groq
if 'def call_groq(prompt):\r\n    import requests\r\n' in c:
    c = c.replace('def call_groq(prompt):\r\n    import requests\r\n', 'def call_groq(prompt):\r\n')
    # Replace requests.post with _http.post inside call_groq
    fixes.append('removed inline import requests from call_groq')

# Fix 4: Remove inline `import requests` from call_gemini
if 'def call_gemini(prompt):\r\n    import requests\r\n' in c:
    c = c.replace('def call_gemini(prompt):\r\n    import requests\r\n', 'def call_gemini(prompt):\r\n')
    fixes.append('removed inline import requests from call_gemini')

# Fix 5: Add top-level imports if not there
top_imports_needed = []
if 'import re\r\n' not in c[:500]:
    top_imports_needed.append('import re')
if 'import urllib.parse\r\n' not in c[:500]:
    top_imports_needed.append('import urllib.parse')

print(f"Fixes applied: {fixes}")
print(f"Top imports needed: {top_imports_needed}")

# Check remaining inline imports
remaining = [(i+1, l.strip()) for i,l in enumerate(c.split('\n')) 
             if '    import ' in l and 'import time as' not in l 
             and 'import shutil' not in l and 'import traceback' not in l]
print(f"Remaining inline imports: {remaining[:10]}")

codecs.open('app.py','w','utf-8').write(c)
print("Done")
