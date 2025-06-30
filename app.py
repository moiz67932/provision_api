import os, requests, json
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
from openai import OpenAI
import time, sys


app = Flask(__name__)
CORS(app, origins="*")               # <-- allows localhost Wizard during dev

# ─── Env ──────────────────────────────────────────────────────────────────
SB_URL      = os.environ["SUPABASE_URL"]
SB_KEY      = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI_KEY  = os.environ["OPENAI_KEY"]
GHCR_IMAGE  = os.environ["GHCR_IMAGE"]
RW_TOKEN    = os.environ["RAILWAY_TOKEN"]
PROJECT_ID  = os.environ["RAILWAY_PROJECT_ID"]
ENV_ID     = os.environ["RAILWAY_ENVIRONMENT_ID"] 

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
    service_name = f"dental-agent-{clinic_id}-{int(time.time())}"

    gql = """
    mutation ($input: CreateServiceInput!) {
      createService(input: $input) { id name }
    }
    """

    vars = {
      "input": {
        "projectId": PROJECT_ID,
        "name":      service_name,

        # (1) environment(s) the service will run in
        "serviceEnvironments": [
          { "environmentId": ENV_ID }          # nothing else here
        ],

        # (2) how to run the code – a pre-built container image
        "source": {
          "type": "image",
          "image": {
            "image": GHCR_IMAGE,
            "restartPolicy": "UNLESS_STOPPED"
          }
        },

        # (3) *top-level* environment variables for the container
        "envVars": [
          { "key": "CLINIC_ID",            "value": clinic_id },
          { "key": "SUPABASE_URL",         "value": SB_URL },
          { "key": "SUPABASE_SERVICE_KEY", "value": SB_KEY },
          { "key": "OPENAI_KEY",           "value": OPENAI_KEY },
          # add PG_*, LIVEKIT_*, TWILIO_* here if needed
        ]
      }
    }

    headers = { "Authorization": f"Bearer {RW_TOKEN}" }
    resp = requests.post(
        "https://backboard.railway.app/graphql/v2",
        json={ "query": gql, "variables": vars },
        headers=headers,
    )

    if resp.status_code >= 400:
        # full payload & error for easy debugging
        print("⚠️  Payload sent to Backboard:\n",
              json.dumps(vars, indent=2),
              "\n🚨 Railway 400:\n", resp.text,
              file=sys.stderr, flush=True)

    resp.raise_for_status()
      
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
