import redis
import json
import os

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def run_facefusion(job):
    print("🧠 CPU:", job["user_id"])
    os.system(f"""
    cd /root/bot/project/facefusion &&
    /root/bot/project/venv/bin/python facefusion.py headless-run \
    -s {job['user_photo']} \
    -t {job['banner_path']} \
    -o {job['result_photo']} \
    --execution-providers cpu
    """)

while True:
    _, data = r.brpop("cpu_queue")
    job = json.loads(data)
    run_facefusion(job)