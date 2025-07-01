import os
import requests
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
from openai import OpenAI
import time
import sys

app = Flask(__name__)
CORS(app, origins="*")

# ─── Env ──────────────────────────────────────────────────────────────────
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI_KEY = os.environ["OPENAI_KEY"]
GHCR_IMAGE = os.environ["GHCR_IMAGE"]
SB_URL = os.environ["SUPABASE_URL"].split(';')[0].strip()

FLY_TOKEN = os.environ["FLY_API_TOKEN"]
FLY_APP = os.environ["FLY_APP"]
FLY_REGION = os.getenv("FLY_REGION", "iad")

# ─── Clients ──────────────────────────────────────────────────────────────
supabase = create_client(SB_URL, SB_KEY)
ai = OpenAI(api_key=OPENAI_KEY)

# ─── Helpers ──────────────────────────────────────────────────────────────
def embed(text: str) -> list[float]:
    """Return a 1536-dim embedding using the new OpenAI client."""
    resp = ai.embeddings.create(
        model="text-embedding-3-small",
        input=[text],
    )
    return resp.data[0].embedding

def spin_agent(clinic_id: str):
    name = f"dental-agent-{clinic_id}-{int(time.time())}"
    payload = {
        "name": name,
        "region": FLY_REGION,
        "config": {
            "image": GHCR_IMAGE,
            "cmd": ["python", "agent.py", "dev"],
            "env": {
                "CLINIC_ID": clinic_id,
                "SUPABASE_URL": SB_URL,
                "SUPABASE_SERVICE_KEY": SB_KEY,
                "OPENAI_KEY": OPENAI_KEY,
                "LIVEKIT_URL": os.environ["LIVEKIT_URL"],
                "LIVEKIT_API_KEY": os.environ["LIVEKIT_API_KEY"],
                "LIVEKIT_API_SECRET": os.environ["LIVEKIT_API_SECRET"],
                "TWILIO_ACCOUNT_SID": os.environ["TWILIO_ACCOUNT_SID"],
                "TWILIO_AUTH_TOKEN": os.environ["TWILIO_AUTH_TOKEN"],
            },
            "restart": { "policy": "on-failure" },
            "guest": { "cpu_kind": "shared", "cpus": 1, "memory_mb": 1024 }
        }
    }
    r = requests.post(
        f"https://api.machines.dev/v1/apps/{FLY_APP}/machines",
        headers={
            "Authorization": f"Bearer {FLY_TOKEN}",
            "Content-Type": "application/json"
        },
        json=payload, timeout=30
    )
    if r.status_code >= 400:
        print("Fly error", r.status_code, r.text, flush=True)
    r.raise_for_status()

# ─── Route ────────────────────────────────────────────────────────────────
@app.post("/provision")
def provision():
    data = request.get_json(force=True)
    cid = data["clinic_id"]

    # 1 ▸ fetch the wizard row
    row = (
        supabase
        .from_("dental-clinic-data")
        .select("*")
        .eq("id", cid)
        .single()
        .execute()
    ).data

    # 2 ▸ embed the profile into a vector
    blob = " ".join([
        row.get("name", ""),
        ", ".join(row.get("services", [])),
        ", ".join(row.get("insurances", [])),
        row.get("policies", "")
    ])
    vec = embed(blob)

    # 3 ▸ update the vector and mark as live
    supabase.from_("dental-clinic-data").update({
        "vector": vec,
        "status": "live"
    }).eq("id", cid).execute()

    # 4 ▸ spin the Fly.io agent
    spin_agent(str(cid))

    return jsonify({"ok": True}), 202

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=8080)
