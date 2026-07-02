import requests

url = "http://127.0.0.1:8081/api/v1/auth/login"
resp = requests.post(url, json={"username": "redscarf", "password": "nowandforever"})
token = resp.json()["token"]

url2 = "http://127.0.0.1:8081/api/v1/knowledge/upload?kb_id=test"
files = {'file': ('Doctor.pdf', open('Doctor.pdf', 'rb'), 'application/pdf')}
headers = {'Authorization': f'Bearer {token}'}

resp2 = requests.post(url2, files=files, headers=headers)
print("Status:", resp2.status_code)
print("Body:", resp2.text)
