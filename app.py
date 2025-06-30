import os, requests, json
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
from openai import OpenAI
import time

app = Flask(__name__)
CORS(app, origins="*")               # <-- allows localhost Wizard during dev

# ─── Env ──────────────────────────────────────────────────────────────────
SB_URL      = os.environ["SUPABASE_URL"]
SB_KEY      = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI_KEY  = os.environ["OPENAI_KEY"]
GHCR_IMAGE  = os.environ["GHCR_IMAGE"]
RW_TOKEN    = os.environ["RAILWAY_TOKEN"]
PROJECT_ID  = os.environ["RAILWAY_PROJECT_ID"]

# ─── Clients ──────────────────────────────────────────────────────────────
supabase = create_client(SB_URL, SB_KEY)
ai       = OpenAI(api_key=OPENAI_KEY)

# ─── Helpers ──────────────────────────────────────────────────────────────
def embed(text: str) -> list[float]:
    """Return a 1536-dim embedding using the new OpenAI client."""
    resp = ai.embeddings.create(
        model="text-embedding-3-small",
        input=[text],
    )
    return resp.data[0].embedding

def spin_agent(clinic_id: str):
    """Create a new Railway service that runs the pre-built agent image."""

    # make the name unique on every launch
    service_name = f"dental-agent-{clinic_id}-{int(time.time())}"

    gql = """
    mutation ($input: CreateServiceDeploymentInput!) {
      createServiceDeployment(input: $input) { service { id name } }
    }"""

    vars = {
      "input": {
        "projectId": PROJECT_ID,
        "serviceName": service_name,

        # tell Railway to start a container FROM an existing image
        "source": {
          "type": "image",
          "image": {
            "image": GHCR_IMAGE,                 # ghcr.io/you/dental-agent:latest
            "restartPolicy": "UNLESS_STOPPED"    # or "ON_FAILURE"
          }
        },

        "envVars": [
          {"key": "CLINIC_ID",            "value": clinic_id},
          {"key": "SUPABASE_URL",         "value": SB_URL},
          {"key": "SUPABASE_SERVICE_KEY", "value": SB_KEY},
          {"key": "OPENAI_KEY",           "value": OPENAI_KEY},
          # add PG_*, LIVEKIT_*, TWILIO_* here if needed:
          # {"key": "PG_HOST", "value": os.environ["PG_HOST"]},
        ]
      }
    }

    headers = {"Authorization": f"Bearer {RW_TOKEN}"}
    r = requests.post(
        "https://backboard.railway.app/graphql/v2",
        json={"query": gql, "variables": vars},
        headers=headers,
    )

    if r.status_code >= 400:
        # print full error payload so you can see exactly why it failed
        print("🚨 Railway GraphQL error", r.status_code, r.text, flush=True)

    r.raise_for_status()   # still raise if not 2xx so Flask returns 500
    
    
# ─── Route ────────────────────────────────────────────────────────────────
@app.post("/provision")
def provision():
    data = request.get_json(force=True)
    cid  = data["clinic_id"]          # ← still receives the *integer* id

    # 1 ▸ fetch the wizard row
    row = (supabase
           .from_("dental-clinic-data")      # ← your actual table name
           .select("*")
           .eq("id", cid)                    # id is still int
           .single()
           .execute()).data

    # 2 ▸ embed the concatenated profile
    blob = " ".join([
        row.get("name", ""),
        ", ".join(row.get("services", [])),
        ", ".join(row.get("insurances", [])),
        row.get("policies", ""),
    ])
    vec = embed(blob)

    # 3 ▸ store the vector & mark as live
    supabase.from_("dental-clinic-data").update({
        "vector": vec,
        "status": "live"
    }).eq("id", cid).execute()

    # 4 ▸ spin the agent container
    spin_agent(str(cid))             # service name gets the int id

    return jsonify({"ok": True}), 202

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
