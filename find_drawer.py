import codecs
c = codecs.open('templates/contacts.html','r','utf-8').read()
idx = c.find('function openDrawer')
snippet = c[idx:idx+2000]
# Find fetch calls
import re
fetches = re.findall(r"fetch\(['\"`]([^'\"`]+)['\"`]", snippet)
print("fetch calls in openDrawer:", fetches)
# Also find renderDrawer
idx2 = c.find('function renderDrawer')
print("\nrenderDrawer snippet:")
print(c[idx2:idx2+500])
