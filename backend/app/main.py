from fastapi import FastAPI

app = FastAPI(title="Gold Bar Price Tracker")


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "gold-bar-tracker"}
