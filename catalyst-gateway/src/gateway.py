import uuid

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from .a2a_client import A2AClient
from .config import load_config
from .sidecar_ui.render import render_ask_page


def create_app() -> FastAPI:
    app = FastAPI(title="Catalyst Gateway", version="0.0.1")
    config = load_config()
    client = A2AClient(config.router_url)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict) -> dict:
        return await client.send_chat_completion(payload)

    @app.get("/sidecar", response_class=HTMLResponse)
    async def sidecar_ask_form() -> str:
        return render_ask_page()

    @app.post("/sidecar/ask", response_class=HTMLResponse)
    async def sidecar_ask(question: str = Form(...)) -> str:
        payload = {
            "id": f"sidecar-{uuid.uuid4()}",
            "messages": [{"role": "user", "content": question}],
        }
        response = await client.send_chat_completion(payload)
        return render_ask_page(question=question, response=response)

    return app


app = create_app()
