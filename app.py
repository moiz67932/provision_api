import os, requests, json
from flask import Flask, request, jsonify
from supabase import create_client
import openai

app = Flask(__name__)

SB_URL  = os.environ["SUPABASE_URL"]
SB_KEY  = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI  = os.environ["OPENAI_KEY"]
IMAGE   = os.environ["GHCR_IMAGE"]
RW_TOK  = os.environ["RAILWAY_TOKEN"]
PROJ_ID = os.environ["RAILWAY_PROJECT_ID"]  # Railway injects this

supabase = create_client(SB_URL, SB_KEY)
openai.api_key = OPENAI

def embed(txt: str):
    resp = openai.Embedding.create(input=[txt],
                                   model="text-embedding-3-small")
    return resp.data[0].embedding

def spin_agent(clinic_id: str):
    gql = """
    mutation ($input: CreateServiceDeploymentInput!) {
      createServiceDeployment(input: $input) { service { id } }
    }"""
    vars = {
      "input": {
        "projectId": PROJ_ID,
        "serviceName": f"dental-agent-{clinic_id}",
        "image": IMAGE,
        "envVars": [
          {"key":"CLINIC_ID","value":clinic_id},
          {"key":"SUPABASE_URL","value":SB_URL},
          {"key":"SUPABASE_SERVICE_KEY","value":SB_KEY},
          # add WhatsApp, Twilio, LiveKit keys here as needed
        ],
        "restartPolicy": "ON_FAILURE"
      }
    }
    headers = {"Authorization": f"Bearer {RW_TOK}"}
    requests.post("https://backboard.railway.app/graphql/v2",
                  json={"query": gql, "variables": vars},
                  headers=headers).raise_for_status()

@app.post("/provision")
def provision():
    data = request.get_json(force=True)
    cid  = data["clinic_id"]
    row  = (supabase.from_("clinics")
            .select("*").eq("id", cid).single().execute()).data
    blob = " ".join([
        row.get("name",""), ", ".join(row.get("services",[])),
        ", ".join(row.get("insurances",[])), row.get("policies","")
    ])
    vec  = embed(blob)
    supabase.from_("clinics").update({"vector": vec, "status":"live"})\
             .eq("id", cid).execute()
    spin_agent(cid)
    return jsonify({"ok": True}), 202

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
