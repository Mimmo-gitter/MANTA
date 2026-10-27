from fastapi import FastAPI

app = FastAPI(
    title="Engineering Intelligence Platform",
    version="3.0.0",
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
